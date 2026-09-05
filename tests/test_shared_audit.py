from aura_music_studio.audit import AuditLedger
from aura_music_studio.shared_audit import AuditWriter


def test_audit_writer_reuses_existing_ledger_contract(monkeypatch):
    rows = []
    ledger = object.__new__(AuditLedger)
    monkeypatch.setattr(ledger, "append", lambda **kwargs: rows.append(kwargs) or kwargs)
    writer = AuditWriter(ledger)
    result = writer.write(actor_id="owner", role="owner", action="config.change",
                          target_type="provider", target_id="youtube", correlation_id="corr1",
                          metadata={"status": "enabled"})
    assert result["actor"] == "owner"
    assert result["details"]["correlation_id"] == "corr1"
    assert rows[0]["action"] == "config.change"
