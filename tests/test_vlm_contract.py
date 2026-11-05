from __future__ import annotations

from pathlib import Path

from vlm.describe import describe_event


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content":
                '{"descricao": "pessoa de camiseta escura", '
                '"risk_level": "baixo", "suspeito": false, "motivo": "passagem"}'}}]}


def test_describe_accepts_str_path_and_missing_file(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"not-a-real-jpeg-but-enough-for-base64")
    monkeypatch.setattr("vlm.describe.httpx.post", lambda *a, **k: _Response())

    as_str = describe_event("passagem_pessoa", [str(image)])
    as_path = describe_event("passagem_pessoa", [image])

    assert as_str is not None and as_str.descricao == "pessoa de camiseta escura"
    assert as_path is not None and as_path.risk_level == "baixo"
    assert describe_event("passagem_pessoa", [str(tmp_path / "missing.jpg")]) is None
    assert describe_event("passagem_pessoa", []) is None
