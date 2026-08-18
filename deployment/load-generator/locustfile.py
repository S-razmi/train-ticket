"""Locust load generator for train-ticket, against the gateway
(ts-gateway-service:18888).

Two personas run concurrently, each spawning its own share of virtual
users (Locust's per-User-class `weight`), rather than one single scripted
path - the goal is trace coverage across as much of the ~50-service
application as a synthetic load generator reasonably can reach, not just
one login->book->pay->cancel happy path:

  BookingUser (weight 5) - logs into the shared seeded account once, then
    repeats a mix of weighted tasks for its whole lifetime: plain
    browsing, book+pay+cancel, book+pay+collect+execute (the "ticket
    actually gets used" terminal path, as an alternative to cancelling),
    book+pay+consign (baggage), book+pay+rebook (change trip), adding
    food/insurance add-ons to a booking, food/insurance/transfer-plan
    browsing, and periodic re-login.

  VisitorUser (weight 2) - registers a brand new throwaway account at
    spawn and only browses (search, transfer-plan, non-high-speed-train
    search) - never books, since a freshly registered account has no
    payment balance and every pay call would just fail insufficient-funds.
    Exists to add ts-user-service registration traffic and a second,
    independent source of search traffic.

Why two personas instead of one script doing everything: a single
sequential script only ever produces one shape of traffic per iteration.
Splitting by persona - and letting Locust run many instances of each
concurrently - means the traffic mix itself is diverse (some users only
browsing, others deep in a purchase, others mid-rebook) at any given
moment, which is what actually varies which of the ~50 services show up
in a given trace window, not just which single flow the script happens
to be executing.

Every request/response shape below was verified against the live API
(controllers in ts-auth-service, ts-user-service, ts-travel-service,
ts-travel2-service, ts-travel-plan-service, ts-contacts-service,
ts-preserve-service, ts-order-service, ts-inside-payment-service,
ts-cancel-service, ts-consign-service, ts-rebook-service,
ts-execute-service, ts-food-service, ts-assurance-service), not assumed
from reading code alone - several things that looked right on paper
turned out not to match runtime behavior:

- trips/left's request field is `startPlace`/`endPlace` (no "ing") -
  `startingPlace` is silently accepted and ignored by Jackson, returning
  an empty result with HTTP 200 (no error at all).
- Not every from/to station pair the app "supports" actually has seeded
  trip data behind it: several seeded trips' routeId points at a Route
  that doesn't include the requested destination, so ts-basic-service
  legitimately returns "no travel info available" for plausible-looking
  pairs (e.g. shanghai->taiyuan) even though the trip/route rows
  individually exist, and it's direction-sensitive (nanjing->shanghai
  works, shanghai->nanjing doesn't - route station order matters).
  VERIFIED_ROUTES/VERIFIED_TRAVEL2_ROUTES below were found by directly
  probing every station pair implied by every seeded Route, keeping only
  the ones that actually return trips - not by reading the seed data and
  assuming it's internally consistent.
- preserve's response never contains the created order's id (a bug in
  PreserveServiceImpl - it returns the literal string "Success" as
  `data`), so the order id has to be looked up afterward via
  order/query.
- order/query throws a 500 (NullPointerException in
  StringUtils.String2Date, which unconditionally parses
  travelDateEnd/boughtDateStart/boughtDateEnd whenever
  enableStateQuery=true, regardless of whether those particular
  sub-filters are enabled) unless those three fields are also given
  non-null dummy values - see _find_my_order().
- There is no logout/session-invalidation endpoint anywhere in this
  codebase (checked: neither ts-auth-service nor ts-user-service nor the
  gateway define one; auth is stateless JWT, nothing server-side to
  invalidate). "refresh_session" below re-logs-in to get a fresh token,
  which is the closest real equivalent to a sign-out/sign-in cycle this
  app actually supports - it does not call a "logout" endpoint because
  none exists.
- collect/execute (ts-execute-service) and rebook (ts-rebook-service)
  each move an order into a terminal-ish status (COLLECTED/USED, or
  CHANGE) that a subsequent cancel would then reject - so each is its
  own complete task ending there, not chained onto book_pay_cancel.
- Food ordering's foodType meaning was confirmed directly against
  PreserveServiceImpl: 0 = none, 1 = train delivery (only
  foodName/foodPrice used), 2 = station pickup (stationName/storeName
  also used). Values in STATION_FOOD/TRAIN_FOOD below are the actual
  seeded food/store data for the nanjing<->shanghai G/D trips (pulled
  live from GET /api/v1/foodservice/foods/..., not guessed).
- Assurance has exactly one valid non-zero type: AssuranceType.java
  defines only TRAFFIC_ACCIDENT(1, ..., 3.0) - there is nothing else to
  pick between.

Not covered by either persona (left as a known gap, not silently
ignored): ts-delivery-service, ts-food-delivery-service,
ts-notification-service, ts-wait-order-service, ts-route-plan-service,
ts-order-other-service (booking always creates the order in
ts-order-service regardless of trip prefix - ts-order-other-service is
only reachable via its own direct CRUD endpoints, which mostly no-op
since preserve never populates it), and the ts-admin-* services
(deliberately out of scope - those are the admin dashboard's backend,
not something an end user's browser ever calls).

Configuration (env vars, all optional):
  TT_USERNAME, TT_PASSWORD   - defaults to the seeded fdse_microservice account
  TT_SEAT_TYPE               - default "2" (1st/comfort class); "3" = 2nd/economy

Run directly:
  locust -f locustfile.py --host http://ts-gateway-service.default.svc.cluster.local:18888

Or headless, for a fixed burst:
  locust -f locustfile.py --host http://<gateway> --headless -u 20 -r 5 -t 5m
"""
import os
import random
from datetime import datetime, timedelta

from locust import HttpUser, task, between

USERNAME = os.environ.get("TT_USERNAME", "fdse_microservice")
PASSWORD = os.environ.get("TT_PASSWORD", "111111")
SEAT_TYPE = int(os.environ.get("TT_SEAT_TYPE", "2"))

# order/query's NullPointerException workaround (see module docstring):
# wide-open bounds so the (unwanted but unavoidable) date filters never
# exclude a real order.
_FAR_PAST = "2020-01-01"
_FAR_FUTURE = "2030-01-01"

ORDER_STATUS_NOTPAID = 0

# (from, to) pairs confirmed - by direct probing against a live cluster,
# not derived from reading the seed data - to actually return trips from
# POST /api/v1/travelservice/trips/left. Of ~48 candidate pairs implied
# by every seeded Route's station list (both directions), only these 11
# work; the rest return an empty list with HTTP 200 (see module
# docstring). All lie along the nanjing-zhenjiang-wuxi-suzhou-shanghai
# route plus shanghai-suzhou, and are direction-sensitive.
VERIFIED_ROUTES = [
    ("nanjing", "shanghai"),
    ("nanjing", "zhenjiang"),
    ("nanjing", "wuxi"),
    ("nanjing", "suzhou"),
    ("zhenjiang", "wuxi"),
    ("zhenjiang", "suzhou"),
    ("zhenjiang", "shanghai"),
    ("wuxi", "suzhou"),
    ("wuxi", "shanghai"),
    ("suzhou", "shanghai"),
    ("shanghai", "suzhou"),
]

# Same kind of verification, against POST /api/v1/travel2service/trips/left
# (non-high-speed Z/T/K trains - a separate service/dataset from the G/D
# trains above).
VERIFIED_TRAVEL2_ROUTES = [
    ("taiyuan", "nanjing"),
    ("nanjing", "beijing"),
    ("shanghai", "nanjing"),
    ("shanghai", "taiyuan"),
]

# Verified via POST /api/v1/travelplanservice/travelPlan/transferResult:
# both legs return trips (taiyuan->nanjing via Z1236, nanjing->shanghai
# via the same trip continuing plus the G-trains above).
TRANSFER_PLAN = {"startStation": "taiyuan", "viaStation": "nanjing", "endStation": "shanghai"}

# Real seeded food/store data for the nanjing<->shanghai G/D trips,
# pulled live from GET /api/v1/foodservice/foods/2026-08-19/nanjing/shanghai/G1234
# rather than guessed. Confirmed only seeded for this specific route (a
# food query against any other VERIFIED_ROUTES pair returns "Get All Food
# Failed") - food-related tasks below deliberately restrict themselves to
# this pair rather than any random VERIFIED_ROUTES choice.
FOOD_ROUTE = ("nanjing", "shanghai")
TRAIN_FOOD = [("Egg Soup", 3.2), ("Pork Chop with rice", 9.5)]
STATION_FOOD = [
    ("nanjing", "Burger King", "Big Burger", 1.2),
    ("nanjing", "Pizza Hut", "Bone Soup", 2.5),
    ("nanjing", "McDonald's", "Big Mac", 2.2),
    ("shanghai", "KFC", "Hamburger", 5.0),
    ("shanghai", "Good Taste", "Rice", 1.2),
    ("suzhou", "Roman Holiday", "Big Burger", 1.2),
]

ASSURANCE_NONE = 0
ASSURANCE_TRAFFIC_ACCIDENT = 1  # the only non-zero AssuranceType that exists


def _travel_date():
    # tomorrow, so it's always valid regardless of when this runs (the
    # server rejects departure dates that aren't >= today)
    return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")


class TrainTicketBase(HttpUser):
    """Shared request helpers. Not run directly - both personas below
    subclass this."""

    abstract = True

    def _auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"} if getattr(self, "token", None) else {}

    def _login(self, username, password):
        with self.client.post(
            "/api/v1/users/login",
            json={"username": username, "password": password, "verificationCode": ""},
            catch_response=True,
            name="/api/v1/users/login",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code == 200 and body.get("status") == 1:
                self.token = body["data"]["token"]
                self.account_id = body["data"]["userId"]
            else:
                resp.failure(f"login failed: {body.get('msg', resp.text)}")

    def _register(self, username, password):
        with self.client.post(
            "/api/v1/userservice/users/register",
            json={
                "userName": username,
                "password": password,
                "gender": random.choice([0, 1]),
                "documentType": 1,
                "documentNum": str(random.randint(10**17, 10**18 - 1)),
                "email": f"{username}@locust.test",
            },
            catch_response=True,
            name="/api/v1/userservice/users/register",
        ) as resp:
            body = resp.json() if resp.text else {}
            # registerUser() responds HTTP 201 Created (UserController.java),
            # not 200 - confirmed live, not assumed.
            if resp.status_code != 201 or body.get("status") != 1:
                resp.failure(f"register failed: {body.get('msg', resp.text)}")

    def _search_trips(self, from_station, to_station, travel_date):
        with self.client.post(
            "/api/v1/travelservice/trips/left",
            json={"startPlace": from_station, "endPlace": to_station, "departureTime": travel_date},
            catch_response=True,
            name="/api/v1/travelservice/trips/left",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code != 200 or body.get("status") != 1:
                resp.failure(f"search failed: {body.get('msg', resp.text)}")
                return []
            trips = body.get("data") or []
            if not trips:
                resp.failure(f"no trips returned for {from_station}->{to_station}")
            return trips

    def _search_travel2_trips(self, from_station, to_station, travel_date):
        with self.client.post(
            "/api/v1/travel2service/trips/left",
            json={"startPlace": from_station, "endPlace": to_station, "departureTime": travel_date},
            catch_response=True,
            name="/api/v1/travel2service/trips/left",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code != 200 or body.get("status") != 1:
                resp.failure(f"travel2 search failed: {body.get('msg', resp.text)}")
                return []
            trips = body.get("data") or []
            if not trips:
                resp.failure(f"no travel2 trips returned for {from_station}->{to_station}")
            return trips

    def _query_transfer_plan(self, travel_date):
        with self.client.post(
            "/api/v1/travelplanservice/travelPlan/transferResult",
            json={**TRANSFER_PLAN, "travelDate": travel_date, "trainType": ""},
            catch_response=True,
            name="/api/v1/travelplanservice/travelPlan/transferResult",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code != 200 or body.get("status") != 1:
                resp.failure(f"transfer plan failed: {body.get('msg', resp.text)}")


class BookingUser(TrainTicketBase):
    """Logs into the shared seeded account (pre-loaded with a payment
    balance) once, then repeats a mix of weighted tasks for its whole
    lifetime.

    Uses the shared seeded account rather than a unique one per virtual
    user: newly-registered accounts have no payment balance, so pay would
    always fail insufficient-funds for them (see VisitorUser instead).
    The tradeoff is that _find_my_order() matches "my" order by train
    number + route + most recent boughtDate among NOTPAID orders on the
    shared account, which is theoretically racy under very high
    concurrency (another virtual user's just-created order could match
    first). Fine for a load generator whose job is producing realistic
    traffic shape, not for correctness-critical financial testing -
    confirmed by direct load testing earlier: at 15 concurrent users this
    raced on 2 of 312 requests, surfaced as a clean Locust failure, not a
    silent wrong-order action.
    """

    weight = 5
    wait_time = between(1, 3)

    def on_start(self):
        self.token = None
        self.account_id = None
        self.contacts_id = None
        self._login(USERNAME, PASSWORD)
        if self.account_id:
            self._ensure_contact()

    def _ensure_contact(self):
        with self.client.get(
            f"/api/v1/contactservice/contacts/account/{self.account_id}",
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/contactservice/contacts/account/[accountId]",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code == 200 and body.get("status") == 1 and body.get("data"):
                self.contacts_id = body["data"][0]["id"]
                resp.success()
            else:
                resp.failure(f"no existing contacts: {body.get('msg', resp.text)}")

        if self.contacts_id:
            return

        with self.client.post(
            "/api/v1/contactservice/contacts",
            json={
                "accountId": self.account_id,
                "name": f"locust-{random.randint(0, 999999)}",
                "documentType": 1,
                "documentNumber": str(random.randint(10**17, 10**18 - 1)),
                "phoneNumber": f"1{random.randint(10**9, 10**10 - 1)}",
            },
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/contactservice/contacts",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code == 200 and body.get("status") == 1:
                self.contacts_id = body["data"]["id"]
            else:
                resp.failure(f"contact creation failed: {body.get('msg', resp.text)}")

    def _reserve(self, from_station, to_station, trip, travel_date, assurance=ASSURANCE_NONE, food=None):
        trip_id = f"{trip['tripId']['type']}{trip['tripId']['number']}"
        body = {
            "accountId": self.account_id,
            "contactsId": self.contacts_id,
            "tripId": trip_id,
            "seatType": random.choice([2, 3]),
            "loginToken": self.token,
            "date": travel_date,
            "from": from_station,
            "to": to_station,
            "assurance": assurance,
            "foodType": 0,
            "stationName": "",
            "storeName": "",
            "foodName": "",
            "foodPrice": 0,
            "handleDate": "",
            "consigneeName": "",
            "consigneePhone": "",
            "consigneeWeight": 0,
            "withIn": False,
        }
        if food == "train":
            name, price = random.choice(TRAIN_FOOD)
            body.update(foodType=1, foodName=name, foodPrice=price)
        elif food == "station":
            station, store, name, price = random.choice(STATION_FOOD)
            body.update(foodType=2, stationName=station, storeName=store, foodName=name, foodPrice=price)

        with self.client.post(
            "/api/v1/preserveservice/preserve",
            json=body,
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/preserveservice/preserve",
        ) as resp:
            resp_body = resp.json() if resp.text else {}
            if resp.status_code == 200 and resp_body.get("status") == 1:
                return trip_id
            resp.failure(f"reserve failed: {resp_body.get('msg', resp.text)}")
            return None

    def _find_my_order(self, from_station, to_station, trip_id):
        with self.client.post(
            "/api/v1/orderservice/order/query",
            json={
                "loginId": self.account_id,
                "enableStateQuery": True,
                "state": ORDER_STATUS_NOTPAID,
                # required to dodge a NullPointerException server-side -
                # see module docstring
                "travelDateStart": _FAR_PAST,
                "travelDateEnd": _FAR_FUTURE,
                "boughtDateStart": _FAR_PAST,
                "boughtDateEnd": _FAR_FUTURE,
            },
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/orderservice/order/query",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code != 200 or body.get("status") != 1:
                resp.failure(f"order query failed: {body.get('msg', resp.text)}")
                return None
            candidates = [
                o for o in (body.get("data") or [])
                if o.get("trainNumber") == trip_id
                and o.get("from") == from_station
                and o.get("to") == to_station
            ]
            if not candidates:
                resp.failure("no matching order found after reserve")
                return None
            candidates.sort(key=lambda o: o["boughtDate"], reverse=True)
            return candidates[0]["id"]

    def _pay(self, order_id, trip_id):
        with self.client.post(
            "/api/v1/inside_pay_service/inside_payment",
            json={"orderId": order_id, "userId": self.account_id, "tripId": trip_id, "price": ""},
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/inside_pay_service/inside_payment",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code != 200 or body.get("status") != 1:
                resp.failure(f"pay failed: {body.get('msg', resp.text)}")
                return False
            return True

    def _cancel(self, order_id):
        with self.client.get(
            f"/api/v1/cancelservice/cancel/{order_id}/{self.account_id}",
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/cancelservice/cancel/[orderId]/[loginId]",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code != 200 or body.get("status") != 1:
                resp.failure(f"cancel failed: {body.get('msg', resp.text)}")

    def _consign(self, order_id, from_station, to_station, travel_date):
        with self.client.post(
            "/api/v1/consignservice/consigns",
            json={
                "orderId": order_id,
                "accountId": self.account_id,
                "handleDate": travel_date,
                "targetDate": travel_date,
                "from": from_station,
                "to": to_station,
                "consignee": f"locust-{random.randint(0, 999999)}",
                "phone": f"1{random.randint(10**9, 10**10 - 1)}",
                "weight": round(random.uniform(1.0, 20.0), 1),
                "isWithin": True,
            },
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/consignservice/consigns",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code != 200 or body.get("status") != 1:
                resp.failure(f"consign failed: {body.get('msg', resp.text)}")

    def _rebook(self, order_id, old_trip_id, new_trip, travel_date):
        new_trip_id = f"{new_trip['tripId']['type']}{new_trip['tripId']['number']}"
        with self.client.post(
            "/api/v1/rebookservice/rebook",
            json={
                "loginId": self.account_id,
                "orderId": order_id,
                "oldTripId": old_trip_id,
                "tripId": new_trip_id,
                "seatType": SEAT_TYPE,
                "date": travel_date,
            },
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/rebookservice/rebook",
        ) as resp:
            body = resp.json() if resp.text else {}
            # status 2 = "pay the difference" (a valid outcome, not a
            # failure - just one this task doesn't chase further)
            if resp.status_code != 200 or body.get("status") not in (1, 2):
                resp.failure(f"rebook failed: {body.get('msg', resp.text)}")

    def _collect_and_execute(self, order_id):
        with self.client.get(
            f"/api/v1/executeservice/execute/collected/{order_id}",
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/executeservice/execute/collected/[orderId]",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code != 200 or body.get("status") != 1:
                resp.failure(f"collect failed: {body.get('msg', resp.text)}")
                return

        with self.client.get(
            f"/api/v1/executeservice/execute/execute/{order_id}",
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/executeservice/execute/execute/[orderId]",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code != 200 or body.get("status") != 1:
                resp.failure(f"execute failed: {body.get('msg', resp.text)}")

    def _book_and_pay(self, assurance=ASSURANCE_NONE, food=None, route=None):
        """Shared first half of every purchase task. Returns
        (from_station, to_station, trip_id, order_id, travel_date) or
        None on any failure along the way. `route` overrides the random
        VERIFIED_ROUTES choice - used for food add-ons, which only have
        seeded data on FOOD_ROUTE (see book_pay_cancel)."""
        from_station, to_station = route or random.choice(VERIFIED_ROUTES)
        travel_date = _travel_date()

        trips = self._search_trips(from_station, to_station, travel_date)
        if not trips:
            return None

        trip = random.choice(trips)
        trip_id = self._reserve(from_station, to_station, trip, travel_date, assurance=assurance, food=food)
        if not trip_id:
            return None

        order_id = self._find_my_order(from_station, to_station, trip_id)
        if not order_id:
            return None

        if not self._pay(order_id, trip_id):
            return None

        return from_station, to_station, trip_id, order_id, travel_date

    @task(6)
    def browse_only(self):
        """A user who searches but doesn't book - most real traffic."""
        from_station, to_station = random.choice(VERIFIED_ROUTES)
        self._search_trips(from_station, to_station, _travel_date())

    @task(4)
    def book_pay_cancel(self):
        # weighted toward no food add-on; food only has seeded data on
        # FOOD_ROUTE, so picking it also pins the route (see _book_and_pay)
        food = random.choice([None, None, "train", "station"])
        result = self._book_and_pay(
            assurance=random.choice([ASSURANCE_NONE, ASSURANCE_TRAFFIC_ACCIDENT]),
            food=food,
            route=FOOD_ROUTE if food else None,
        )
        if result is None:
            return
        _from, _to, _trip_id, order_id, _date = result
        self._cancel(order_id)

    @task(2)
    def book_pay_collect_execute(self):
        result = self._book_and_pay()
        if result is None:
            return
        _from, _to, _trip_id, order_id, _date = result
        self._collect_and_execute(order_id)

    @task(2)
    def book_pay_consign(self):
        result = self._book_and_pay()
        if result is None:
            return
        from_station, to_station, _trip_id, order_id, travel_date = result
        self._consign(order_id, from_station, to_station, travel_date)

    @task(2)
    def book_pay_rebook(self):
        result = self._book_and_pay()
        if result is None:
            return
        from_station, to_station, trip_id, order_id, travel_date = result

        new_trips = self._search_trips(from_station, to_station, travel_date)
        new_trips = [t for t in new_trips if f"{t['tripId']['type']}{t['tripId']['number']}" != trip_id]
        if not new_trips:
            return
        self._rebook(order_id, trip_id, random.choice(new_trips), travel_date)

    @task(2)
    def browse_food_and_assurance(self):
        """Read-only browsing of the add-on services, independent of any
        booking - adds ts-food-service/ts-train-food-service/
        ts-station-food-service/ts-assurance-service coverage without the
        overhead of a full purchase. Pinned to FOOD_ROUTE - see its
        definition for why."""
        from_station, to_station = FOOD_ROUTE
        travel_date = _travel_date()
        trips = self._search_trips(from_station, to_station, travel_date)
        if trips:
            trip = random.choice(trips)
            trip_id = f"{trip['tripId']['type']}{trip['tripId']['number']}"
            with self.client.get(
                f"/api/v1/foodservice/foods/{travel_date}/{from_station}/{to_station}/{trip_id}",
                headers=self._auth_headers(),
                catch_response=True,
                name="/api/v1/foodservice/foods/[date]/[from]/[to]/[tripId]",
            ) as resp:
                body = resp.json() if resp.text else {}
                if resp.status_code != 200 or body.get("status") != 1:
                    resp.failure(f"food query failed: {body.get('msg', resp.text)}")

        with self.client.get(
            "/api/v1/assuranceservice/assurances/types",
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/assuranceservice/assurances/types",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"assurance types query failed: HTTP {resp.status_code}")

    @task(1)
    def browse_transfer_plan(self):
        self._query_transfer_plan(_travel_date())

    @task(1)
    def refresh_session(self):
        """The closest real equivalent to a sign-out/sign-in cycle this
        app supports - see module docstring on why there's no logout
        call here."""
        self._login(USERNAME, PASSWORD)
        if self.account_id:
            self._ensure_contact()


class VisitorUser(TrainTicketBase):
    """Registers a brand-new throwaway account at spawn and only browses.
    Never books: a freshly registered account has no payment balance, so
    pay would always fail insufficient-funds. Exists to add
    ts-user-service registration traffic and a second, independent
    source of search traffic running concurrently with BookingUser."""

    weight = 2
    wait_time = between(1, 4)

    def on_start(self):
        self.token = None
        self.account_id = None
        username = f"locust_visitor_{random.randint(0, 10**9)}"
        self._register(username, PASSWORD)
        self._login(username, PASSWORD)

    @task(4)
    def browse_random_route(self):
        if not self.account_id:
            return
        from_station, to_station = random.choice(VERIFIED_ROUTES)
        self._search_trips(from_station, to_station, _travel_date())

    @task(2)
    def browse_travel2(self):
        if not self.account_id:
            return
        from_station, to_station = random.choice(VERIFIED_TRAVEL2_ROUTES)
        self._search_travel2_trips(from_station, to_station, _travel_date())

    @task(1)
    def browse_transfer_plan(self):
        if not self.account_id:
            return
        self._query_transfer_plan(_travel_date())
