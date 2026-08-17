"""Locust load generator for train-ticket: simulates the realistic
multi-step user journey against the gateway (ts-gateway-service:18888) -
login, search trains, reserve a ticket, pay, cancel.

Every request/response shape here was verified against the live API
(controllers in ts-auth-service, ts-travel-service, ts-contacts-service,
ts-preserve-service, ts-order-service, ts-inside-payment-service,
ts-cancel-service), not assumed from reading code alone - a few things
that looked right on paper turned out not to match runtime behavior:

- trips/left's request field is `startPlace`/`endPlace` (no "ing") -
  `startingPlace` is silently accepted and ignored by Jackson, returning
  an empty result with HTTP 200 (no error at all).
- Not every from/to station pair the app "supports" actually has seeded
  trip data behind it: several seeded trips' routeId points at a Route
  that doesn't include the requested destination, so ts-basic-service
  legitimately returns "no travel info available" for e.g.
  shanghai->taiyuan even though the trips/route rows individually exist.
  nanjing->shanghai and shanghai->suzhou are confirmed to have working
  data end to end.
- preserve's response never contains the created order's id (a bug in
  PreserveServiceImpl - it returns the literal string "Success" as
  `data`), so the order id has to be looked up afterward via
  order/query.
- order/query throws a 500 (NullPointerException in
  StringUtils.String2Date, which unconditionally parses
  travelDateEnd/boughtDateStart/boughtDateEnd whenever
  enableStateQuery=true, regardless of whether those particular
  sub-filters are enabled) unless those three fields are also given
  non-null dummy values - see _find_my_order() below.

Uses the shared seeded account (fdse_microservice, pre-loaded with a
payment balance) rather than registering a unique account per virtual
user: newly-registered accounts have no payment balance, so the pay step
would always fail insufficient-funds for them. The tradeoff is that
_find_my_order() matches "my" order by train number + route + most
recent boughtDate among NOTPAID orders on the shared account, which is
theoretically racy under very high concurrency (another virtual user's
just-created order could match first). Fine for a load generator whose
job is producing realistic traffic shape, not for correctness-critical
financial testing.

Configuration (env vars, all optional):
  TT_USERNAME, TT_PASSWORD   - defaults to the seeded fdse_microservice account
  TT_FROM_STATION            - default "nanjing"
  TT_TO_STATION              - default "shanghai"
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
FROM_STATION = os.environ.get("TT_FROM_STATION", "nanjing")
TO_STATION = os.environ.get("TT_TO_STATION", "shanghai")
SEAT_TYPE = int(os.environ.get("TT_SEAT_TYPE", "2"))

# order/query's NullPointerException workaround (see module docstring):
# wide-open bounds so the (unwanted but unavoidable) date filters never
# exclude a real order.
_FAR_PAST = "2020-01-01"
_FAR_FUTURE = "2030-01-01"

ORDER_STATUS_NOTPAID = 0


def _travel_date():
    # tomorrow, so it's always valid regardless of when this runs (the
    # server rejects departure dates that aren't >= today)
    return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")


class TrainTicketUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.token = None
        self.account_id = None
        self.contacts_id = None
        self._login()
        if self.account_id:
            self._ensure_contact()

    def _auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _login(self):
        with self.client.post(
            "/api/v1/users/login",
            json={"username": USERNAME, "password": PASSWORD, "verificationCode": ""},
            catch_response=True,
            name="/api/v1/users/login",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code == 200 and body.get("status") == 1:
                self.token = body["data"]["token"]
                self.account_id = body["data"]["userId"]
            else:
                resp.failure(f"login failed: {body.get('msg', resp.text)}")

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

    def _search_trips(self, travel_date):
        with self.client.post(
            "/api/v1/travelservice/trips/left",
            json={"startPlace": FROM_STATION, "endPlace": TO_STATION, "departureTime": travel_date},
            catch_response=True,
            name="/api/v1/travelservice/trips/left",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code != 200 or body.get("status") != 1:
                resp.failure(f"search failed: {body.get('msg', resp.text)}")
                return []
            trips = body.get("data") or []
            if not trips:
                resp.failure(
                    f"no trips returned for {FROM_STATION}->{TO_STATION} "
                    "(check that this station pair has seeded trip data)"
                )
            return trips

    def _reserve(self, trip, travel_date):
        trip_id = f"{trip['tripId']['type']}{trip['tripId']['number']}"
        with self.client.post(
            "/api/v1/preserveservice/preserve",
            json={
                "accountId": self.account_id,
                "contactsId": self.contacts_id,
                "tripId": trip_id,
                "seatType": SEAT_TYPE,
                "loginToken": self.token,
                "date": travel_date,
                "from": FROM_STATION,
                "to": TO_STATION,
                "assurance": 0,
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
            },
            headers=self._auth_headers(),
            catch_response=True,
            name="/api/v1/preserveservice/preserve",
        ) as resp:
            body = resp.json() if resp.text else {}
            if resp.status_code == 200 and body.get("status") == 1:
                return trip_id
            resp.failure(f"reserve failed: {body.get('msg', resp.text)}")
            return None

    def _find_my_order(self, trip_id):
        with self.client.post(
            "/api/v1/orderservice/order/query",
            json={
                "loginId": self.account_id,
                "enableStateQuery": True,
                "state": ORDER_STATUS_NOTPAID,
                # required to dodge a NullPointerException server-side - see
                # module docstring
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
                and o.get("from") == FROM_STATION
                and o.get("to") == TO_STATION
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

    @task(3)
    def browse_only(self):
        """A user who searches but doesn't book - most real traffic."""
        if not self.account_id:
            return
        self._search_trips(_travel_date())

    @task(1)
    def book_pay_cancel(self):
        """The full purchase journey: search -> reserve -> pay -> cancel."""
        if not self.account_id or not self.contacts_id:
            return

        travel_date = _travel_date()
        trips = self._search_trips(travel_date)
        if not trips:
            return

        trip = random.choice(trips)
        trip_id = self._reserve(trip, travel_date)
        if not trip_id:
            return

        order_id = self._find_my_order(trip_id)
        if not order_id:
            return

        self._pay(order_id, trip_id)
        self._cancel(order_id)
