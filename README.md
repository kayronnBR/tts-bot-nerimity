# TTS Bot PT-BR para Nerimity

Bot de texto-a-fala para Nerimity, feito para rodar no **Termux (Android)**.
Ele entra na chamada de voz, lê mensagens em **CAPSLOCK** e fala o nome da
pessoa na primeira mensagem (e quando a pessoa muda).

## Funcionalidades

- Comando `-tts` entra/sai da call de voz.
- Lê **somente mensagens em caixa alta** (ex.: `OLA MARILENE`).
- Fala `nome disse: mensagem` na primeira vez; mensagens seguintes do mesmo
  usuário são faladas sem o nome.
- Áudio sem cortes: usa relógio absoluto de 20 ms por frame e buffer de 10 s.
- Conecta automaticamente com quem já estava na call (não precisa sair/entrar).
- Cache de áudio (até 50 falas) para repetir frases instantaneamente.
- Reinicia sozinho se cair (loop no tmux + watchdog).

## Requisitos

### No celular (Termux)

Instale os pacotes do sistema:

```bash
pkg update -y
pkg install -y python tmux clang make libffi openssl libjpeg-turbo
```

> `clang`, `make`, `libffi` e `openssl` são necessários porque o `av` (PyAV)
> e o `cryptography` podem precisar compilar no Termux.

### Dependências Python

Testado com **Python 3.14.10** e as versões de `requirements.txt`:

```bash
pip install -r requirements.txt
```

Principais pacotes:

| Pacote | Versão testada | Uso |
|---|---|---|
| `nerimity` | 1.6.2 | SDK do Nerimity (gateway + REST) |
| `aiortc` | 1.16.0 | WebRTC (envio do áudio na call) |
| `av` | 19.0.2 | Decodificar MP3 para PCM |
| `numpy` | 2.5.5 | Processar os samples de áudio |
| `gTTS` | 2.5.4 | Motor de voz rápido (padrão) |
| `edge-tts` | 7.3.1 | Motor alternativo (voz AntonioNeural) |

## Instalação

1. Clone ou copie os arquivos para o Termux:

```bash
mkdir -p ~/tts-bot ~/bots
cp TTS.py start.sh stop.sh ~/tts-bot/
cp watchdog.sh ~/bots/
chmod +x ~/tts-bot/start.sh ~/tts-bot/stop.sh ~/bots/watchdog.sh
```

2. Instale as dependências:

```bash
cd ~/tts-bot
pip install -r requirements.txt
```

3. Configure o token do bot (veja abaixo).

4. Inicie:

```bash
bash ~/tts-bot/start.sh
```

## Configuração

### Token do bot

Em `TTS.py`, linha 28:

```python
TOKEN = "SEU_TOKEN_AQUI"
```

Cole o token do seu bot Nerimity entre as aspas.

> **Segurança:** se for publicar no GitHub, troque por variável de ambiente.
> O repositório já traz um `.env.example` com `NERIMITY_TOKEN`. Nesse caso,
> altere a linha para:
>
> ```python
> TOKEN = os.getenv("NERIMITY_TOKEN", "")
> ```
>
> e crie `~/tts-bot/.env` com `NERIMITY_TOKEN=seu_token` (o `.env` está no
> `.gitignore` e não deve ser enviado ao GitHub).

### Outras opções (topo do `TTS.py`)

```python
VOZ = "pt-BR-AntonioNeural"   # usada se TTS_ENGINE = "edge"
TTS_ENGINE = "gtts"           # "gtts" (rápido) ou "edge" (voz más natural)
GTTS_LANG = "pt-br"
GTTS_TLD = "com.br"
MAX_CACHE_TTS = 50            # quantidade de falas em cache
MAX_QUEUE_FRAMES = 500        # buffer de áudio (10 s)
USUARIOS_BLOQUEADOS = set()   # IDs que o bot ignora
```

## Como usar

No canal de texto:

| Comando | Ação |
|---|---|
| `-tts` | Entra na call (ou sai, se já estiver dentro) |
| `-tts <id_do_canal_de_voz>` | Entra em um canal de voz específico |
| `TESTE` (caixa alta) | O bot fala "SeuNome disse: TESTE" |
| `OUTRA MENSAGEM` | O bot fala só a mensagem (sem repetir o nome) |

Mensagens com letras minúsculas, links ou comandos são ignoradas.

## Operação

```bash
# Iniciar / parar
bash ~/tts-bot/start.sh
bash ~/tts-bot/stop.sh

# Ver logs ao vivo
tail -f ~/tts-bot/bot.log

# Ver a tela do bot
tmux attach -t nerimity

# Status de todos os bots (vídeo, TTS, música)
bash ~/bots/watchdog.sh status

# watchdog (reinicia bots que caíram a cada 30 min)
bash ~/bots/watchdog.sh start
bash ~/bots/watchdog.sh stop
```

O `start.sh` usa `termux-wake-lock` para impedir que o Android suspenda o
processo e mantém o bot sempre rodando dentro da sessão `nerimity` do tmux.

## Estrutura

```
tts-bot/
├── TTS.py        # código do bot
├── start.sh      # inicia em tmux com auto-restart
├── stop.sh       # para a sessão tmux
└── bot.log       # logs (gerado automaticamente)

bots/
├── watchdog.sh   # monitora os bots
└── watchdog.conf # lista de bots monitorados
```

## Solução de problemas

**O bot não fala nada**
- Veja `tail -f ~/tts-bot/bot.log`. Procure por `👉 Falando na call`.
- Se aparecer `⚠️ Erro ao gerar TTS`, rode `pip install -U gTTS edge-tts`.

**A voz sai cortada**
- Confirme `MAX_QUEUE_FRAMES = 500` e que o `recv()` usa
  `self._proximo += FRAME_MS / 1000` (relógio absoluto).

**O bot entra mas não dá som (precisa sair e voltar da call)**
- Verifique se `GerenciadorSalas.usuarios_voz` e
  `VoiceSession.conectar_usuarios()` estão presentes no código.

**Erro `list.index(x): x not in list` ou `have-local-offer`**
- Já tratado: offers remotos são ignorados quando há offer local pendente.

**`Event loop is closed` ao reiniciar**
- É residual de conexões antigas ao parar o bot; use sempre
  `bash ~/tts-bot/stop.sh` antes de `start.sh` (nunca `pkill -f TTS.py`).

## Licença

Uso pessoal. Ajuste o token e as configurações antes de publicar.
