# 🤖 Nerimity TTS Voice Bot

Bot para o [Nerimity](https://nerimity.com) que **entra automaticamente em um canal de voz** e **fala em voz alta** (usando TTS da Microsoft/Edge) qualquer mensagem escrita **EM CAPS LOCK** em um canal de texto.

O áudio é transmitido **de verdade para dentro da chamada de voz** via WebRTC (não é só tocar um som no computador que roda o bot) — outras pessoas na call ouvem o bot falando.

---

## ⚠️ Aviso importante

A Nerimity não possui uma API oficial e documentada para "bot entra na call e manda áudio". As chamadas de voz funcionam com **WebRTC em malha (mesh)**, sinalizado por Socket.IO. Este bot foi construído fazendo engenharia reversa do `nerimity_sdk` e do [cliente web oficial da Nerimity](https://github.com/Nerimity/nerimity-web) para replicar esse comportamento.

Ou seja: funciona hoje, mas **não é um recurso oficialmente suportado**. Se a Nerimity mudar o protocolo de sinalização no futuro, pode ser necessário ajustar o código.

---

## ✨ Funcionalidades

- Conecta ao Nerimity usando o [`nerimity_sdk`](https://pypi.org/project/nerimity_sdk/) oficial.
- Entra automaticamente em um canal de voz configurado.
- Escuta um canal de texto e detecta mensagens 100% em **CAIXA ALTA**.
- Gera a fala com [`edge-tts`](https://github.com/rany2/edge-tts) (voz neural da Microsoft, gratuita).
- Transmite o áudio ao vivo para todos os participantes conectados na chamada via WebRTC (`aiortc`).

---

## 📋 Pré-requisitos

- Python **3.9 ou superior**
- Uma conta/token de bot no Nerimity ([como criar um bot](https://docs.nerimity.com))
- Acesso à internet (para os servidores STUN/TURN e para gerar o TTS)
- (Opcional, mas recomendado) `ffmpeg` instalado no sistema

---

## 🚀 Instalação — passo a passo

### 1. Crie um ambiente virtual (recomendado)

```bash
python3 -m venv venv
source venv/bin/activate      # Linux/macOS
# venv\Scripts\activate       # Windows
```

### 2. Instale as dependências

```bash
pip install -r requirements.txt
```

Se preferir instalar manualmente, sem o `requirements.txt`:

```bash
pip install nerimity_sdk aiortc av numpy edge-tts
```

> 💡 O `aiortc` e o `av` já trazem o `ffmpeg` embutido para decodificar áudio, então normalmente não é preciso instalar nada a mais no sistema. Se aparecer algum erro relacionado a `libav`/`ffmpeg` faltando, instale o ffmpeg do seu sistema:
>
> ```bash
> # Ubuntu/Debian
> sudo apt install ffmpeg
>
> # macOS (Homebrew)
> brew install ffmpeg
> ```

### 3. Pegue o token do seu bot

1. Acesse as configurações de desenvolvedor da sua conta na Nerimity: `https://nerimity.com/app/settings/developer`
2. Crie um bot e copie o **token**.
3. Convide o bot para o seu servidor com permissão para ler mensagens e entrar em canais de voz.

### 4. Pegue os IDs dos canais

No app da Nerimity, ative o **modo desenvolvedor** (se disponível) ou copie o ID pela URL do canal ao clicar nele. Você vai precisar de:

- ID do **canal de texto** que o bot vai "escutar".
- ID do **canal de voz** que o bot vai entrar.

### 5. Configure o `bot.py`

Abra o arquivo `bot.py` e edite o topo com suas informações:

```python
TOKEN = "SEU_TOKEN_AQUI"
CANAL_TEXTO_ID = "ID_DO_CANAL_DE_TEXTO"
CANAL_VOZ_ID = "ID_DO_CANAL_DE_VOZ"
VOZ = "pt-BR-AntonioNeural"  # voz do TTS (veja a lista abaixo)
```

### 6. Rode o bot

```bash
python bot.py
```

Se tudo der certo, você verá algo como:

```
🔄 Iniciando o Bot com o SDK oficial...
✅ Conectado como SeuBot
🔊 Entrou no canal de voz ...
```

Agora é só mandar uma mensagem **EM CAPS LOCK** no canal de texto configurado — o bot vai gerar o áudio e falar na call.

---

## 🎙️ Trocando a voz do TTS

O `edge-tts` suporta várias vozes em português e outros idiomas. Para listar todas as vozes disponíveis:

```bash
edge-tts --list-voices | grep pt-BR
```

Exemplos de vozes em português do Brasil:

| Voz | Gênero |
|---|---|
| `pt-BR-AntonioNeural` | Masculina |
| `pt-BR-FranciscaNeural` | Feminina |

Basta trocar o valor de `VOZ` no topo do `bot.py`.

---

## 🗂️ Estrutura do projeto

```
.
├── bot.py             # código principal do bot
├── requirements.txt   # dependências Python
└── README.md          # esta documentação
```

---

## 🔧 Como funciona (visão geral técnica)

1. O bot conecta ao gateway (WebSocket) da Nerimity usando o `nerimity_sdk`.
2. Assim que fica pronto (`evento ready`), chama o endpoint REST `POST /channels/{id}/voice/join` para entrar no canal de voz.
3. Para cada participante já presente ou que entra depois, o bot negocia uma conexão **WebRTC ponto a ponto** (oferta/resposta SDP + candidatos ICE), trocando essas mensagens pelos eventos de socket `voice:signal_send` / `voice:signal_received` — exatamente como o cliente web oficial faz internamente com a biblioteca `simple-peer`.
4. Quando uma mensagem em CAIXA ALTA chega no canal de texto configurado, o bot gera o áudio com `edge-tts`, decodifica para PCM com `av` (PyAV) e envia esse áudio por dentro de cada conexão WebRTC ativa, usando `aiortc`.

---

## ❓ Solução de problemas

**`ModuleNotFoundError: No module named 'av'` (ou `aiortc`, `edge_tts`, `nerimity_sdk`)**
→ As dependências não foram instaladas no ambiente Python que você está usando para rodar o bot. Rode `pip install -r requirements.txt` dentro do mesmo venv/interpretador usado para executar o `bot.py`.

**O bot entra na call mas ninguém ouve nada**
→ Verifique no console as linhas `[RTC] <usuario>: <estado>`. Se o estado nunca chega a `connected`, pode ser um problema de conectividade ICE/TURN (rede com firewall restritivo, por exemplo).

**O bot lê a própria mensagem e entra em loop**
→ Confirme que o token configurado é o do bot correto e que o evento `ready` está disparando (deve aparecer `✅ Conectado como ...` no console).

**Erro relacionado a `ffmpeg`/`libav`**
→ Instale o ffmpeg no sistema operacional (veja o passo 3 acima).

---

## 📜 Licença

Escolha e adicione a licença de sua preferência (MIT, GPL, etc.) em um arquivo `LICENSE`.
