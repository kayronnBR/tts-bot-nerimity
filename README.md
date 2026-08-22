
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

```

### 2. Instale as dependências

```bash
~/venv/bin/pip install nerimity_sdk aiortc av numpy edge-tts

```

Se preferir instalar manualmente, sem o `requirements.txt`:

```bash
pip install nerimity_sdk aiortc av numpy edge-tts

```

> 💡 O `aiortc` e o `av` já trazem o `ffmpeg` embutido para decodificar áudio, então normalmente não é preciso instalar nada a mais no sistema. Se aparecer algum erro relacionado a `libav`/`ffmpeg` faltando, instale o ffmpeg do seu sistema:
> ```bash
> # Ubuntu/Debian
> sudo apt install ffmpeg
> 
> # macOS (Homebrew)
> brew install ffmpeg
> 
> ```
> 
> 

### 3. Pegue o token do seu bot

1. Acesse as configurações de desenvolvedor da sua conta na Nerimity: `https://nerimity.com/app/settings/developer`
2. Crie um bot e copie o **token**.
3. Convide o bot para o seu servidor com permissão para ler mensagens e entrar em canais de voz.

### 4. Pegue os IDs dos canais

No app da Nerimity, ative o **modo desenvolvedor** (se disponível) ou copie o ID pela URL do canal ao clicar nele. Você vai precisar de:

* ID do **canal de texto** que o bot vai "escutar".
* ID do **canal de voz** que o bot vai entrar.

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

## 📦 O que tem no código

O arquivo `bot.py` traz uma estrutura completa para gerenciar conexões de áudio via WebRTC e múltiplas salas simultâneas de forma dinâmica:

1. **Configurações Globais (`TOKEN`, `VOZ`, `ICE_SERVERS`)**:
* Define o token de acesso do Nerimity, a voz padrão do Microsoft Edge TTS (`pt-BR-AntonioNeural`) e os servidores STUN/TURN (retirados do cliente web oficial da Nerimity) para estabelecer conexões WebRTC P2P.


2. **Sistema de Segurança e Lista Negra (`USUARIOS_BLOQUEADOS`)**:
* Conjunto (`set`) com IDs de usuários impedidos de utilizar comandos interativos na DM do bot.


3. **Comando Mestre por Senha (`COMANDO_MESTRE`, `SENHA_MESTRE`)**:
* Mecanismo de emergência via DM (`!master <senha>`) que desliga o bot de todas as chamadas ativas de uma só vez, independente de quem enviou ou se o usuário está na lista de bloqueados.


4. **Classe `TTSAudioTrack` (MediaStreamTrack)**:
* Uma faixa de áudio customizada estendida do `aiortc` que emite silêncio contínuo até receber amostras de áudio PCM. Possui proteção por fila (`MAX_QUEUE_FRAMES`) para descartar pacotes antigos caso uma conexão WebRTC trave, evitando estouro de memória RAM.


5. **Classe `VoiceSession**`:
* Responsável por gerenciar a presença e o ciclo de vida do bot em **um canal de voz**. Controla a negociação SDP (Oferta/Resposta), troca de candidatos ICE, descarte de áudio recebido dos usuários (para economizar memória, já que o bot só transmite) e um *watchdog* de timeout para fechar conexões mortas/travadas.


6. **Classes `Sala`, `FluxoConfig` e `GerenciadorSalas**`:
* Permitem o gerenciamento de **múltiplas salas simultâneas** (pares de canal de texto + canal de voz) em servidores diferentes.
* Gerenciam o fluxo conversacional passo a passo via DM (Mensagem Direta) quando um usuário digita `!config`.


7. **Função `decodificar_mp3_para_pcm**`:
* Converte o arquivo MP3 gerado pelo `edge-tts` em fluxo de áudio PCM 16-bit Mono 48kHz usando o PyAV (`av`), rodando em uma thread assíncrona separada para não travar o loop do bot.



---

## ⚙️ Como o bot funciona (Fluxo de Execução)

O funcionamento interno do bot combina a API REST do Nerimity, eventos via Socket.IO/Gateway, WebRTC P2P e conversão de texto para fala:

```
[ Usuário digita em CAPS LOCK no canal de texto ]
                       │
                       ▼
         [ Evento 'message:created' ]
                       │
                       ▼
        [ Verifica se o texto é ISUPPER ]
                       │
                       ▼
    [ Gera MP3 via edge-tts (Microsoft Neural) ]
                       │
                       ▼
   [ Decodifica MP3 para PCM 48kHz (PyAV) ]
                       │
                       ▼
 [ Injeta áudio na fila do TTSAudioTrack no WebRTC ]
                       │
                       ▼
[ Transmite áudio ao vivo pros usuários na call ]

```

### Passo a passo detalhado:

1. **Conexão e Autenticação**:
* O bot conecta no gateway WebSocket do Nerimity através do `nerimity_sdk`.


2. **Configuração Interativa via DM**:
* Um usuário envia `!config` na DM do bot.
* O bot responde pedindo o **ID do canal de texto** e em seguida o **ID do canal de voz**.
* O bot chama o endpoint REST `/channels/{id}/voice/join` para sinalizar sua entrada no canal de voz.


3. **Sinalização e Conexão WebRTC (Mesh)**:
* Quando um usuário entra no canal de voz (`voice:user_joined`), o bot escuta o evento e cria uma instância `RTCPeerConnection` (`aiortc`).
* O bot cria uma oferta SDP (*offer*) e envia ao usuário via evento `voice:signal_send`.
* O usuário responde com uma *answer* e candidatos ICE via `voice:signal_received`.
* Uma conexão P2P direta de áudio é estabelecida entre o bot e o participante.


4. **Detecção e Geração de Fala**:
* Ao receber uma mensagem em um canal de texto monitorado (`message:created`), o bot valida se o texto está **100% EM CAIXA ALTA** (`conteudo.isupper()`) e se não foi enviada por ele mesmo.
* O texto é formatado no padrão: `"<Nome do Autor> disse: <MENSAGEM>"`.
* O `edge-tts` baixa a fala em formato MP3 temporário.
* O arquivo MP3 é decodificado para amostras brutas em PCM (48000 Hz, 16-bit, mono) via PyAV (`av`).
* As amostras são enviadas em quadros de 20ms para o `TTSAudioTrack` de cada participante conectado na chamada WebRTC.


5. **Comandos Interativos em DM**:
* `!config`: Inicia a criação de uma nova sala de voz/texto.
* `!sair <id_canal_voz>`: Desconecta o bot e encerra a sala especificada.
* `!master <senha>`: Comando mestre global que força o encerramento e desconexão de todas as salas ativas de uma só vez.



---

## 🎙️ Trocando a voz do TTS

O `edge-tts` suporta várias vozes em português e outros idiomas. Para listar todas as vozes disponíveis:

```bash
edge-tts --list-voices | grep pt-BR

```

Exemplos de vozes em português do Brasil:

| Voz | Gênero |
| --- | --- |
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

**Erro relacionado a `ffmpeg`/`libav**`
→ Instale o ffmpeg no sistema operacional (veja o passo 3 acima).

---

## 📜 Licença

Escolha e adicione a licença de sua preferência (MIT, GPL, etc.) em um arquivo `LICENSE`.

```

```
