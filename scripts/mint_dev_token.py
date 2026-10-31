#!/usr/bin/env python3
"""SOMENTE TESTE: emite um JWT de desenvolvimento para o Miloco.

O server_config.yaml do Miloco roda com o SECRET_KEY padrão
('your-super-secret-key-change-this'), então dá pra assinar um token válido
sem saber o PIN. Use isso só para testar o pipeline localmente.

Segurança: troque o SECRET_KEY do Miloco em produção e remova este script.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

import jwt

SECRET = sys.argv[1] if len(sys.argv) > 1 else "your-super-secret-key-change-this"
now = datetime.utcnow()
token = jwt.encode(
    {"sub": "admin", "exp": now + timedelta(hours=12), "iat": int(time.time())},
    SECRET,
    algorithm="HS256",
)
print(token)
