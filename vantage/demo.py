"""Self-contained demo: builds a realistic multi-service git repo with competing unmerged
branches, ingests it, and runs a few workflows — so the UI is populated and the
change-collision radar has real overlaps to show on first open. Fully offline."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .ingest import ingest_repo
from .orchestration import run_workflow
from .state import RequestKind, WorkflowState

FILES = {
    "services/orders.py": (
        "class OrderService:\n"
        "    def create_order(self, user_id, amount):\n"
        "        fee = self.calc_fee(amount)\n"
        "        return self.charge(user_id, amount + fee)\n\n"
        "    def calc_fee(self, amount):\n"
        "        return round(amount * 0.02, 2)\n\n"
        "    def charge(self, user_id, total):\n"
        "        return {'user': user_id, 'total': total}\n"
    ),
    "services/refunds.py": (
        "class RefundService:\n"
        "    def refund(self, order_id, amount):\n"
        "        return self.reverse(order_id, amount)\n\n"
        "    def reverse(self, order_id, amount):\n"
        "        return {'order': order_id, 'refunded': amount}\n"
    ),
    "services/discount.py": (
        "class DiscountService:\n"
        "    def apply_discount(self, amount, pct):\n"
        "        return round(amount * (1 - pct / 100.0), 2)\n"
    ),
    "api/controller.py": (
        "from services.orders import OrderService\n\n"
        "class PaymentController:\n"
        "    @app.route('/orders', methods=['POST'])\n"
        "    def create_order(self):\n"
        "        return OrderService().create_order(1, 100)\n\n"
        "    @app.route('/refunds', methods=['POST'])\n"
        "    def refund(self):\n"
        "        return {}\n"
    ),
    "db/schema.sql": (
        "CREATE TABLE orders (id INT PRIMARY KEY, user_id INT, total NUMERIC);\n"
        "CREATE TABLE refunds (id INT PRIMARY KEY, order_id INT, amount NUMERIC);\n"
    ),
    "README.md": "# PaymentHub demo service\nOrders, refunds and discounts.\n",
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def build_demo_repo(dest: Path) -> Path:
    repo = Path(dest)
    if (repo / ".git").exists():
        return repo
    repo.mkdir(parents=True, exist_ok=True)
    for rel, body in FILES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(repo, "init", "-q", "-b", "env/dev")
    _git(repo, "config", "user.email", "demo@vantage.dev")
    _git(repo, "config", "user.name", "Vantage Demo")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "PaymentHub: orders, refunds, discount, api, schema")

    # competing branch #1: overlaps orders.py + discount.py (will collide with discount requests)
    _git(repo, "checkout", "-q", "-b", "feature/instant-discount")
    (repo / "services" / "discount.py").write_text(
        (repo / "services" / "discount.py").read_text()
        + "\n    def apply_promo(self, amount, code):\n        return amount  # WIP promo codes\n")
    (repo / "services" / "orders.py").write_text(
        (repo / "services" / "orders.py").read_text().replace(
            "return self.charge(user_id, amount + fee)",
            "return self.charge(user_id, amount + fee)  # TODO hook discount"))
    _git(repo, "commit", "-qam", "wip: instant discount + promo codes")

    # competing branch #2: refunds rounding fix (overlaps refund requests)
    _git(repo, "checkout", "-q", "env/dev")
    _git(repo, "checkout", "-q", "-b", "fix/refund-rounding")
    (repo / "services" / "refunds.py").write_text(
        (repo / "services" / "refunds.py").read_text().replace(
            "return {'order': order_id, 'refunded': amount}",
            "return {'order': order_id, 'refunded': round(amount, 2)}"))
    _git(repo, "commit", "-qam", "fix: round refund amounts to 2dp")

    _git(repo, "checkout", "-q", "env/dev")
    return repo


DEMO_REQUESTS = [
    ("Add instant discount to create_order in the orders service", "feature"),
    ("Fix refund rounding in the refund service", "bug"),
    ("Refactor calc_fee for clarity", "refactor"),
]


def seed(platform, run_workflows: bool = True) -> dict:
    repo = build_demo_repo(platform.settings.data_dir / "demo" / "paymenthub")
    stats = ingest_repo(platform, str(repo), base_branch="env/dev")
    ran = []
    if run_workflows:
        for req, kind in DEMO_REQUESTS:
            st = WorkflowState(request=req, repo="paymenthub", base_branch="env/dev",
                               kind=RequestKind(kind))
            run_workflow(platform, st)
            ran.append({"request_id": st.request_id, "decision": st.review.get("decision"),
                        "collisions": len(st.active_changes)})
    return {"repo": str(repo), "ingest": stats, "runs": ran}
