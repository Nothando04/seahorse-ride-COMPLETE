"""Data structures used for searching and ordering bookings: a binary search tree (BST) and a graph search (BFS).

BinarySearchTree
    Each node has a key, a left child and a right child. The BST rule: every key in a node's LEFT subtree is
    smaller than the node's key, and every key in its RIGHT subtree is larger. Insertion and search start at
    the root and go left or right at each node, so they cost O(h), where h is the tree's height:
    O(log n) when the tree is balanced, O(n) when it is skewed (e.g. keys inserted already sorted).

BookingTree(BinarySearchTree)
    The Seahorse Ride use: bookings keyed by ride date and time. An in-order traversal lists rides in
    date order, and ``between(start, end)`` finds the rides in a date range while skipping whole subtrees
    that are out of range.

status_path(start, goal)
    Breadth-first search (BFS) over the booking state machine ``TRANSITIONS`` (an adjacency list stored in
    a dictionary of sets), using a FIFO queue (``collections.deque``). It returns the shortest sequence of
    status changes from one status to another.
"""
from __future__ import annotations

from collections import deque
from datetime import date, datetime
from typing import Any, Iterator


class TreeNode:
    """One node of the tree: a key, the values stored under it, and links to two children."""

    def __init__(self, key: Any, value: Any = None):
        self.key = key
        self.values: list = [] if value is None else [value]   # several bookings may share one ride time
        self.left: TreeNode | None = None
        self.right: TreeNode | None = None

    @property
    def is_leaf(self) -> bool:
        return self.left is None and self.right is None

    def __repr__(self) -> str:
        return f"TreeNode({self.key!r})"


class BinarySearchTree:
    """An unbalanced binary search tree. Duplicate keys are not added twice (their value joins the node)."""

    def __init__(self):
        self.root: TreeNode | None = None
        self._size = 0

    # ------------------------------------------------------------------ building
    @classmethod
    def from_values(cls, values) -> "BinarySearchTree":
        tree = cls()
        for v in values:
            tree.insert(v)
        return tree

    def insert(self, key: Any, value: Any = None) -> list:
        """Insert ``key``; returns the path of keys compared on the way down (the insertion trace)."""
        path: list = []
        if self.root is None:
            self.root = TreeNode(key, value)
            self._size = 1
            return path
        node = self.root
        while True:
            path.append(node.key)
            if key == node.key:                       # duplicate: keep one node, remember the extra value
                if value is not None:
                    node.values.append(value)
                return path
            if key < node.key:
                if node.left is None:
                    node.left = TreeNode(key, value)
                    break
                node = node.left
            else:
                if node.right is None:
                    node.right = TreeNode(key, value)
                    break
                node = node.right
        self._size += 1
        return path

    # ------------------------------------------------------------------ searching
    def search(self, key: Any) -> tuple[TreeNode | None, list]:
        """Find ``key``. Returns (node or None, keys visited in order). At most height + 1 comparisons."""
        path: list = []
        node = self.root
        while node is not None:
            path.append(node.key)
            if key == node.key:
                return node, path
            node = node.left if key < node.key else node.right
        return None, path

    def __contains__(self, key: Any) -> bool:      # lets us write `60 in tree`
        return self.search(key)[0] is not None

    def __len__(self) -> int:                      # lets us write `len(tree)`
        return self._size

    def __iter__(self) -> Iterator:                 # `for key in tree` gives keys in sorted order
        return iter(self.inorder())

    def minimum(self) -> Any:
        node = self.root
        while node is not None and node.left is not None:
            node = node.left
        return node.key if node else None

    def maximum(self) -> Any:
        node = self.root
        while node is not None and node.right is not None:
            node = node.right
        return node.key if node else None

    def between(self, low: Any, high: Any) -> list[TreeNode]:
        """Nodes with low <= key <= high, in order. Skips subtrees that cannot contain matches."""
        out: list[TreeNode] = []

        def walk(node: TreeNode | None) -> None:
            if node is None:
                return
            if low < node.key:                         # only go left if smaller keys could still match
                walk(node.left)
            if low <= node.key <= high:
                out.append(node)
            if node.key < high:                        # only go right if larger keys could still match
                walk(node.right)

        walk(self.root)
        return out

    # ------------------------------------------------------------------ traversals (recursive, depth-first)
    def inorder(self) -> list:
        """Left, node, right — gives the keys in ascending (sorted) order."""
        out: list = []

        def walk(node: TreeNode | None) -> None:
            if node:
                walk(node.left)
                out.append(node.key)
                walk(node.right)

        walk(self.root)
        return out

    def preorder(self) -> list:
        """Node, left, right — the root comes first; inserting keys in this order rebuilds the same tree."""
        out: list = []

        def walk(node: TreeNode | None) -> None:
            if node:
                out.append(node.key)
                walk(node.left)
                walk(node.right)

        walk(self.root)
        return out

    def postorder(self) -> list:
        """Left, right, node — the root comes last; children are visited before their parent."""
        out: list = []

        def walk(node: TreeNode | None) -> None:
            if node:
                walk(node.left)
                walk(node.right)
                out.append(node.key)

        walk(self.root)
        return out

    def level_order(self) -> list[list]:
        """Breadth-first: the keys on each level, top to bottom (uses a FIFO queue)."""
        levels: list[list] = []
        queue = deque([(self.root, 0)]) if self.root else deque()
        while queue:
            node, depth = queue.popleft()
            if depth == len(levels):
                levels.append([])
            levels[depth].append(node.key)
            for child in (node.left, node.right):
                if child is not None:
                    queue.append((child, depth + 1))
        return levels

    # ------------------------------------------------------------------ shape
    def height(self) -> int:
        """Number of EDGES on the longest root-to-leaf path. Empty tree = -1, one node = 0."""
        def h(node: TreeNode | None) -> int:
            return -1 if node is None else 1 + max(h(node.left), h(node.right))
        return h(self.root)

    def depth(self, key: Any) -> int | None:
        """Number of edges from the root to ``key`` (root = 0); None if absent."""
        node, path = self.search(key)
        return len(path) - 1 if node else None

    def leaves(self) -> list:
        return [n.key for n in self.nodes() if n.is_leaf]

    def nodes(self) -> list[TreeNode]:
        out: list[TreeNode] = []

        def walk(node: TreeNode | None) -> None:
            if node:
                walk(node.left)
                out.append(node)
                walk(node.right)

        walk(self.root)
        return out

    def is_skewed(self) -> bool:
        """True when the tree has degenerated into a line (height = n - 1), e.g. after sorted inserts."""
        return self._size > 2 and self.height() == self._size - 1

    def parent_of(self, key: Any) -> Any:
        node, path = self.search(key)
        return path[-2] if node is not None and len(path) > 1 else None

    def layout(self) -> tuple[list[dict], list[dict]]:
        """Positions for drawing: x = in-order rank, y = depth. Returns (nodes, edges)."""
        nodes: list[dict] = []
        edges: list[dict] = []
        counter = [0]

        def walk(node: TreeNode | None, depth: int) -> None:
            if node is None:
                return
            walk(node.left, depth + 1)
            me = {"key": node.key, "label": self.label(node.key), "x": counter[0], "y": depth,
                  "leaf": node.is_leaf, "count": len(node.values)}
            counter[0] += 1
            nodes.append(me)
            walk(node.right, depth + 1)

        walk(self.root, 0)
        by_key = {n["key"]: n for n in nodes}

        def link(node: TreeNode | None) -> None:
            if node is None:
                return
            for child in (node.left, node.right):
                if child is not None:
                    edges.append({"from": by_key[node.key], "to": by_key[child.key]})
                    link(child)

        link(self.root)
        return nodes, edges

    def label(self, key: Any) -> str:
        """How a key is shown. Child classes override this (polymorphism)."""
        return str(key)


class BookingTree(BinarySearchTree):
    """A BST of bookings keyed by ride date and start time — the Seahorse Ride use of the tree."""

    @classmethod
    def from_bookings(cls, bookings) -> "BookingTree":
        tree = cls()
        for b in bookings:
            when = b.slot.date if b.slot else b.preferred_date
            start = (b.slot.start_time if b.slot else b.preferred_time) or "00:00"
            tree.insert(datetime.combine(when, datetime.strptime(start, "%H:%M").time()), b)
        return tree

    def label(self, key: datetime) -> str:           # overrides BinarySearchTree.label
        return f"{key:%d %b %H:%M}"

    def rides_between(self, start: date, end: date) -> list:
        """All bookings riding from ``start`` to ``end`` inclusive, in date order."""
        low = datetime.combine(start, datetime.min.time())
        high = datetime.combine(end, datetime.max.time())
        return [b for node in self.between(low, high) for b in node.values]

    def rides_in_order(self) -> list:
        return [b for node in self.nodes() for b in node.values]


def parse_keys(text: str, limit: int = 40) -> list:
    """Turn '50, 30, 70' into [50, 30, 70]. Whole numbers if every value is one, otherwise words."""
    parts = [p.strip() for p in text.replace(";", ",").replace("\n", ",").replace(" ", ",").split(",") if p.strip()]
    parts = parts[:limit]
    try:
        return [int(p) for p in parts]
    except ValueError:
        return [p.lower() for p in parts]


# ---------------------------------------------------------------------- graph search over the booking statuses
def status_path(start: str, goal: str, transitions: dict[str, set[str]] | None = None) -> list[str] | None:
    """Shortest list of statuses from ``start`` to ``goal`` using breadth-first search; None if unreachable."""
    if transitions is None:
        from .workflow import TRANSITIONS as transitions
    if start == goal:
        return [start]
    queue = deque([start])                 # FIFO: statuses discovered but not yet explored
    came_from: dict[str, str | None] = {start: None}
    while queue:
        current = queue.popleft()
        for nxt in sorted(transitions.get(current, set())):   # sorted = the same answer every time
            if nxt in came_from:
                continue
            came_from[nxt] = current
            if nxt == goal:                                    # rebuild the path backwards
                path = [goal]
                while came_from[path[-1]] is not None:
                    path.append(came_from[path[-1]])
                return path[::-1]
            queue.append(nxt)
    return None
