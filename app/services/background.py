"""Asynchronous Background task runner for reconciliation and audit cleanup."""
import threading
import time
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.entities import Tenant, Subscription, ProcessedWebhook

class BackgroundWorker:
    def __init__(self, interval_seconds: int = 60):
        self.interval = interval_seconds
        self._running = False
        self._thread = None
        self.stats = {
            "reconciliations_run": 0,
            "subscriptions_audited": 0,
            "last_run": None,
            "status": "idle"
        }

    def start(self):
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False

    def _run_loop(self):
        while self._running:
            self.run_reconciliation_once()
            time.sleep(self.interval)

    def run_reconciliation_once(self):
        """Runs audit on subscription status consistency and logs mismatches."""
        db: Session = SessionLocal()
        try:
            subs = db.query(Subscription).all()
            for sub in subs:
                # Ensure tenant status matches subscription status
                if sub.status == "past_due" and sub.tenant.status != "past_due":
                    sub.tenant.status = "past_due"
                    db.commit()
                elif sub.status == "active" and sub.tenant.status == "past_due":
                    sub.tenant.status = "active"
                    db.commit()
            self.stats["reconciliations_run"] += 1
            self.stats["subscriptions_audited"] = len(subs)
            self.stats["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.stats["status"] = "healthy"
        except Exception as e:
            self.stats["status"] = f"error: {str(e)}"
        finally:
            db.close()

background_worker = BackgroundWorker(interval_seconds=300)
