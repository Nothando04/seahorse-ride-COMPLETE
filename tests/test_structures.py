"""Binary search tree, BookingTree, BFS over the status graph, and the PaymentGateway class hierarchy."""
from datetime import date, datetime

import pytest

from seahorse.gateways import (GATEWAYS, PayfastGateway, PaymentGateway, PaystackGateway, SimulatorGateway,
                               get_gateway)
from seahorse.structures import BinarySearchTree, BookingTree, parse_keys, status_path
from seahorse.workflow import CONFIRMED, DECLINED, PENDING, REFUNDED, TRANSITIONS
from conftest import login


# --------------------------------------------------------------------------- BST
def test_bst_construction_traversals_and_shape():
    t = BinarySearchTree.from_values([50, 30, 70, 20, 40, 60, 80])
    assert t.root.key == 50 and len(t) == 7
    assert t.inorder() == [20, 30, 40, 50, 60, 70, 80]          # sorted
    assert t.preorder() == [50, 30, 20, 40, 70, 60, 80]
    assert t.postorder() == [20, 40, 30, 60, 80, 70, 50]
    assert t.level_order() == [[50], [30, 70], [20, 40, 60, 80]]
    assert t.height() == 2 and t.leaves() == [20, 40, 60, 80]
    assert t.depth(60) == 2 and t.parent_of(60) == 70 and t.minimum() == 20 and t.maximum() == 80
    assert not t.is_skewed()
    # Rebuilding from the pre-order sequence gives the same tree
    assert BinarySearchTree.from_values(t.preorder()).preorder() == t.preorder()


def test_bst_insert_trace_search_and_duplicates():
    t = BinarySearchTree()
    assert t.insert(50) == []                      # root: no comparisons
    assert t.insert(30) == [50]
    assert t.insert(40) == [50, 30]                # 40 < 50 go left, 40 > 30 go right
    node, path = t.search(40)
    assert node.key == 40 and path == [50, 30, 40]
    node, path = t.search(35)
    assert node is None and path == [50, 30, 40]   # fell off at 40's empty left child
    t.insert(40)
    assert len(t) == 3                             # duplicates are not added twice
    assert 40 in t and 99 not in t and list(t) == [30, 40, 50]


def test_sorted_input_makes_a_skewed_tree():
    t = BinarySearchTree.from_values([10, 20, 30, 40, 50])
    assert t.is_skewed() and t.height() == 4       # height n - 1: search degrades to O(n)
    assert t.search(50)[1] == [10, 20, 30, 40, 50]


def test_empty_tree_and_word_keys():
    t = BinarySearchTree()
    assert t.height() == -1 and t.inorder() == [] and t.search(1) == (None, []) and t.minimum() is None
    words = BinarySearchTree.from_values(parse_keys("storm, biscuit, willow, atlas"))
    assert words.inorder() == ["atlas", "biscuit", "storm", "willow"]
    assert parse_keys("50 30\n70;20") == [50, 30, 70, 20]


def test_range_query_skips_out_of_range_subtrees():
    t = BinarySearchTree.from_values([50, 30, 70, 20, 40, 60, 80])
    assert [n.key for n in t.between(35, 65)] == [40, 50, 60]


class _B:
    """Stand-in booking with just what BookingTree needs."""

    def __init__(self, ref, d, t):
        self.reference, self.preferred_date, self.preferred_time, self.slot = ref, d, t, None


def test_booking_tree_orders_rides_and_finds_a_week():
    rides = [_B("C", date(2026, 12, 3), "10:30"), _B("A", date(2026, 12, 1), "08:00"),
             _B("B", date(2026, 12, 1), "16:30"), _B("D", date(2026, 12, 20), "08:00"),
             _B("A2", date(2026, 12, 1), "08:00")]
    tree = BookingTree.from_bookings(rides)
    assert [b.reference for b in tree.rides_in_order()] == ["A", "A2", "B", "C", "D"]
    assert [b.reference for b in tree.rides_between(date(2026, 12, 1), date(2026, 12, 7))] == ["A", "A2", "B", "C"]
    assert tree.label(datetime(2026, 12, 1, 8, 0)) == "01 Dec 08:00"           # overridden label()
    assert BinarySearchTree().label(datetime(2026, 12, 1, 8, 0)) == "2026-12-01 08:00:00"


# --------------------------------------------------------------------------- BFS
def test_status_path_bfs_finds_shortest_route():
    assert status_path(PENDING, CONFIRMED) == [PENDING, "Approved — Awaiting Payment", CONFIRMED]
    path = status_path(PENDING, REFUNDED)
    assert path[0] == PENDING and path[-1] == REFUNDED
    for a, b in zip(path, path[1:]):
        assert b in TRANSITIONS[a]                 # every step is an allowed transition
    assert status_path(DECLINED, CONFIRMED) is None   # a final status has no way out
    assert status_path(PENDING, PENDING) == [PENDING]


# --------------------------------------------------------------------------- gateways
def test_gateway_hierarchy_and_dynamic_dispatch():
    with pytest.raises(TypeError):
        PaymentGateway({})                         # abstract: cannot be created
    assert issubclass(SimulatorGateway, PayfastGateway) and issubclass(PayfastGateway, PaymentGateway)
    cfg = {"PAYMENT_PROVIDER": "paystack", "PAYSTACK_SECRET_KEY": "sk_test_x"}
    g = get_gateway(cfg)
    assert isinstance(g, PaystackGateway) and g.ready and g.describe() == "Paystack — ready"
    assert get_gateway({"PAYMENT_PROVIDER": "paystack"}).describe() == "Paystack — not set up"
    assert type(get_gateway({"PAYMENT_PROVIDER": "unknown"})) is PayfastGateway
    # Each class has its own start_checkout (overridden), except the simulator, which inherits PayFast's
    assert PaystackGateway.start_checkout is not PayfastGateway.start_checkout
    assert SimulatorGateway.start_checkout is PayfastGateway.start_checkout
    assert set(GATEWAYS) == {"paystack", "payfast", "simulator"}
    with pytest.raises(RuntimeError):
        SimulatorGateway({"APP_ENV": "production"})


# --------------------------------------------------------------------------- staff page
def test_data_structures_page(client, app):
    login(client, "guide@test.io")
    html = client.get("/admin/data-structures?values=50,30,70,20,40,60,80&find=60").get_data(as_text=True)
    assert "20, 30, 40, 50, 60, 70, 80" in html and "50, 30, 20, 40, 70, 60, 80" in html
    assert "50 → 70 → 60" in html and "found" in html
    html = client.get("/admin/data-structures?values=10,20,30,40").get_data(as_text=True)
    assert "Skewed" in html
    html = client.get("/admin/data-structures?from_status=Declined&to_status=Refunded").get_data(as_text=True)
    assert "No path" in html
    login(client, "thandi@example.com", staff=False)
    assert app.test_client().get("/admin/data-structures").status_code == 404   # visitors can't see it


# --------------------------------------------------------------------------- sliding-window rate limiter (deque)
def test_rate_limiter_blocks_the_eleventh_sign_in(app):
    from seahorse.security import reset_rate_limits
    app.config["RATE_LIMIT_IN_TESTS"] = True
    reset_rate_limits()
    c = app.test_client()
    codes = []
    for _ in range(11):
        token = c.get("/account/sign-in").get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
        codes.append(c.post("/account/sign-in", data={"email": "nobody@example.com", "password": "wrong",
                                                      "csrf_token": token}).status_code)
    reset_rate_limits()
    assert codes[:10] == [200] * 10 and codes[10] == 429
