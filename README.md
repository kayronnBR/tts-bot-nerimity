# 🤖 Bot TTS Nerimity

Um bot para o **Nerimity** que converte mensagens de texto enviadas em caixa alta em áudio (*Text-to-Speech*), utilizando a voz neural em português da Microsoft, e reproduz o som no seu sistema.

---

## ⚠️ Requisitos e Regras de Funcionamento

* **Uso em Call:** Para que os membros do servidor ou da chamada ouçam o áudio do bot, você **deve obrigatoriamente estar conectado a um canal de voz no Nerimity** e redirecionar a saída de áudio do sistema para a sua transmissão ou microfone virtual.
* **Mensagens em CAPSLOCK:** O bot lê **apenas** mensagens enviadas inteiramente em caixa alta (**CAPSLOCK**). Mensagens enviadas em letras minúsculas ou misturadas serão ignoradas.

---

## 🛠️ Instalação e Pré-requisitos

### 1. Dependências do Sistema (Linux Mint)
Instale o **qpwgraph** (gerenciador gráfico de áudio do PipeWire) via loja de aplicativos ou Flatpak:

* **Download:** [Flathub - qpwgraph](https://flathub.org/en/apps/org.rncbc.qpwgraph)

### 2. Configuração do Ambiente Virtual Python
Crie o ambiente virtual no seu repositório local e instale as bibliotecas necessárias:

```bash
# Cria o ambiente virtual
python3 -m venv ~/venv 

# Ativa o ambiente e instala as dependências
~/venv/bin/pip install pygame edge-tts nerimity-sdk

```

---

## 📁 Estrutura de Arquivos

Certifique-se de salvar o código do bot no caminho especificado abaixo (ou no diretório de sua preferência):

```plaintext
/home/user/bot.py

```

---

## configurando token e canal onde o bot vai ler

abre o arquivo bot.py e procure:
TOKEN = "XXXXX"
CANAL_ID = "XXXXX"

onde tem os XXX altere para os codigo que vai pedir.

sobre o token você precisa criar um bot no nerimity para monitorar o chat para gerar o audio
1- https://nerimity.com/app/settings/developer/applications
2- click em "adicionar aplicativo"
3- defina o nome dele e volta para tela dos aplicativos
4- selecione o bot que você criou
5- click em "usuario do bot"
6- copie o token e cole no arquivo do bot.py

exemplo do resultado como vai ficar:
TOKEN = "5M3vVeGvBDQbrFAMUJem"

e para pegar o id do canal é facil
1- entre no seu servidor
2- click direito no canal
3- "copiar id"

agora volte no bot,py e altere, exemplo do resultado final
CANAL_ID = "5M3vVeGvBDQbrFAMUJem"

## 🎧 Configuração de Áudio (PipeWire + qpwgraph)

Para redirecionar o som do bot para o Nerimity:

1. Inicie a execução do bot no terminal.
2. Abra o aplicativo **qpwgraph**.
3. No mapa de conexões visuais do **qpwgraph**:
* Localize o nó de saída de áudio referente ao `python3` ou `pygame`.
* Arraste a conexão de saída do bot até o nó de entrada do **Nerimity** (ou para o seu microfone virtual/loopback conectado ao canal de voz).


4. **Atenção:** Desconecte ou desligue o seu microfone principal no canal se não quiser que ele interfira na transmissão do bot.

---

## ▶️ Como Rodar o Bot

Execute o comando no terminal utilizando o interpretador do ambiente virtual criado:

```bash
~/venv/bin/python3 /home/user/bot.py

```

Quando a inicialização for concluída, o terminal exibirá:

```text
🔄 Iniciando o Bot com o SDK oficial...

```

Envie qualquer texto em **MAIÚSCULAS** no canal configurado para que o bot faça a leitura no formato:
