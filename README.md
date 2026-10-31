# casa-monitor

Monitoramento inteligente por visão computacional para a câmera externa (Xiaomi CW700S), com relatório diário de segurança às 22h00 entregue no Telegram.

## O que faz

- **Captura** o stream H.264 da câmera via WebSocket do [Miloco](https://github.com/XiaoMi/xiaomi-miloco) (sem alterar a stack de captura)
- **Camada 1**: YOLO11n + ByteTrack (GPU) continuamente, com amostragem configurável de FPS
- **Camada 2**: máquina de estados por evento — passagens (pessoa/veículo/animal), aproximação do portão, acesso à casa (entrada/saída + tempo de permanência), embarque/desembarque em veículos parados, loitering e horário de risco (00h–05h30)
- **VLM**: 1–3 keyframes por evento → descrição de pessoa/veículo/postura + `risk_level` (OpenAI-compatible — MiMo)
- **Persistência**: SQLite (`data/monitoring.db`) + snapshots com rotação automática (7 dias)
- **Relatório diário 22h00**: consolidação SQL → LLM sintetiza Markdown (alertas, portão/permanência, veículos, animais) → envia no Telegram (`notifier.py` — também WhatsApp/Evolução e e-mail)

## Operação

```bash
# daemon 24/7 (systemd user service, sobrevive a reboot)
systemctl --user status|restart casa-monitor
journalctl --user -u casa-monitor -f

# testes manuais
python main.py --capture-test            # captura real
python main.py --detect-test --seconds 60 # YOLO + eventos
python main.py --generate-report-now      # gera e envia o relatório
python main.py --generate-report-now --no-send  # só gera
```

## Configuração

Copie `.env.example` para `.env` e preencha (o `.env` **nunca** é versionado):

- `MILOCO_LOGIN_CODE` — PIN de 6 dígitos do Miloco (o login envia `MD5(PIN)`)
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — entrega do relatório
- `VLM_API_KEY` / `LLM_API_KEY` — provedor OpenAI-compatible (VLM + síntese do relatório)

## Calibração das zonas

```bash
python zones/calibrate_zones.py --grid    # frame com grade de coordenadas
python zones/calibrate_zones.py --preview # desenha as zonas de config/zones.yaml
python zones/calibrate_zones.py --click   # define polígonos clicando (precisa de display)
```

Zonas em `config/zones.yaml`: `ZONA_RUA`, `ZONA_PORTAO`, `ZONA_INTERNA`, `LINHA_ACESSO_CASA` (linha virtual + `house_side` — o lado de dentro da casa define ENTRY/EXIT).

## Arquitetura

```
[Miloco WS H.264] → capture/ → detect/ (YOLO+ByteTrack) → events/ (máquina de estados)
                                                              │
                    report/ (22h, APScheduler) ← db/ (SQLite) ←┤ → vlm/ (descrição por evento)
                              │                               │
                              └→ notifier.py (Telegram)    snapshots/ (7 dias)
```

Requisitos: Python 3.12, GPU NVIDIA (CUDA) recomendada — fallback CPU automático.
