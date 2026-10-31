"""Cliente HTTP/WS do Miloco — camada de captura (não altera o Miloco).

Contrato (lido do código-fonte do Miloco):
- POST /api/auth/login {"username", "password"} -> seta cookie access_token (JWT)
- GET  /api/miot/camera_list -> lista de câmeras (campo did)
- WS   /api/miot/ws/video_stream?camera_id=<did>&channel=<n>
       autenticação via cookie access_token; mensagens binárias = H.264 (Annex-B)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import ssl
from typing import AsyncIterator, Optional

import httpx
import websockets

from settings import MilocoConfig

logger = logging.getLogger(__name__)


class MilocoAuthError(RuntimeError):
    pass


class MilocoClient:
    def __init__(self, cfg: Optional[MilocoConfig] = None):
        self.cfg = cfg or MilocoConfig()
        self._token: str = self.cfg.token
        self._ssl = None if self.cfg.verify_ssl else ssl._create_unverified_context()

    # ---------- auth ----------
    async def login(self) -> str:
        """Faz login e guarda o JWT (cookie access_token).

        O frontend do Miloco envia password = MD5(PIN) (verificado no bundle
        original) — o servidor guarda o hash, não o PIN cru.
        """
        if self._token:
            logger.info("Usando MILOCO_TOKEN do ambiente")
            return self._token
        if not self.cfg.login_code:
            raise MilocoAuthError(
                "MILOCO_LOGIN_CODE vazio — preencha o PIN de 6 dígitos no .env "
                "(ou defina MILOCO_TOKEN para testes)"
            )
        pwd_hash = hashlib.md5(self.cfg.login_code.encode("utf-8")).hexdigest()
        async with httpx.AsyncClient(verify=self.cfg.verify_ssl, timeout=15) as client:
            resp = await client.post(
                f"{self.cfg.base_url.rstrip('/')}/api/auth/login",
                json={"username": self.cfg.username, "password": pwd_hash},
            )
            if resp.status_code != 200:
                raise MilocoAuthError(f"login falhou: HTTP {resp.status_code} {resp.text[:200]}")
            token = resp.cookies.get("access_token")
            if not token:
                # fallback: alguns builds retornam o token no corpo
                body = resp.json()
                token = (body.get("data") or {}).get("access_token") or (body.get("data") or {}).get("token")
            if not token:
                raise MilocoAuthError("login ok, mas access_token não encontrado na resposta")
            self._token = token
            logger.info("Login no Miloco ok (user=%s)", self.cfg.username)
            return self._token

    def _headers(self) -> dict:
        return {"Cookie": f"access_token={self._token}"}

    # ---------- API ----------
    async def camera_list(self) -> list[dict]:
        async with httpx.AsyncClient(
            verify=self.cfg.verify_ssl, timeout=15, headers=self._headers()
        ) as client:
            resp = await client.get(f"{self.cfg.base_url.rstrip('/')}/api/miot/camera_list")
            resp.raise_for_status()
            data = resp.json().get("data") or []
            return data if isinstance(data, list) else data.get("cameras", [])

    async def resolve_camera_id(self) -> str:
        if self.cfg.camera_id:
            return self.cfg.camera_id
        cameras = await self.camera_list()
        if not cameras:
            raise MilocoAuthError("nenhuma câmera encontrada na conta Mi Home (verifique a região)")
        cam = cameras[0]
        did = cam.get("did") or cam.get("id") or cam.get("device_id")
        if not did:
            raise MilocoAuthError(f"camera_list sem 'did': {cameras[0]}")
        logger.info("Câmera selecionada: %s (%s)", did, cam.get("model", "?"))
        return str(did)

    # ---------- stream ----------
    async def stream_h264_once(self, camera_id: str, channel: Optional[int] = None) -> AsyncIterator[bytes]:
        """Uma conexão direta (sem reconexão automática) — ideal para capturas avulsas."""
        channel = self.cfg.channel if channel is None else channel
        url = f"{self.cfg.ws_url}?camera_id={camera_id}&channel={channel}"
        async with websockets.connect(
            url, additional_headers=self._headers(), ssl=self._ssl, max_size=8 * 1024 * 1024
        ) as conn:
            logger.info("Stream conectado (one-shot)")
            async for message in conn:
                if isinstance(message, (bytes, bytearray)):
                    yield bytes(message)

    async def stream_h264(self, camera_id: str, channel: Optional[int] = None) -> AsyncIterator[bytes]:
        """Abre o WS e gera os chunks binários H.264 recebidos."""
        channel = self.cfg.channel if channel is None else channel
        url = f"{self.cfg.ws_url}?camera_id={camera_id}&channel={channel}"
        logger.info("Conectando stream: %s", url)
        async for conn in websockets.connect(
            url, additional_headers=self._headers(), ssl=self._ssl, max_size=8 * 1024 * 1024
        ):
            try:
                logger.info("Stream conectado")
                async for message in conn:
                    if isinstance(message, (bytes, bytearray)):
                        yield bytes(message)
            except websockets.ConnectionClosed as err:
                logger.warning("WS caiu (%s) — reconectando em 3s", err)
                await asyncio.sleep(3)
