# Monitor simples AWS IoT Core

Projeto em Python para:

- conectar na AWS IoT Core com certificado cliente;
- publicar `#07$` no tópico `PIVO_ID` a cada 30 segundos;
- assinar o tópico `cloudv2-GPS`;
- salvar as respostas em arquivos para acompanhamento em tempo real;
- abrir uma página local para visualização.

## Deploy na Vercel

Este repositório agora está preparado para deploy estático na Vercel com a página em `public/index.html`.

Importante:

- a Vercel hospeda bem a interface;
- o processo MQTT contínuo da AWS IoT Core deve continuar rodando localmente, em uma VM ou em outro serviço persistente;
- os certificados em `KEYS/` não devem ir para um repositório público.
- a página publicada pode consumir os dados do seu monitor local por uma URL pública de túnel.

Arquivos sensíveis e locais já estão ignorados no Git:

- `KEYS/`
- `config.json`
- `data/`

## Arquivos principais

- `monitor.py`: conecta, publica, assina e grava os dados.
- `config.example.json`: modelo de configuração.
- `viewer/index.html`: tela simples de monitoramento.
- `public/index.html`: dashboard hospedado na Vercel que lê `GET /api/live` do seu monitor local.
- `vercel.json`: configuração básica da Vercel.
- `data/messages.jsonl`: histórico completo das mensagens recebidas.
- `data/latest.json`: última mensagem recebida.
- `data/status.json`: estado atual da conexão.

## Como usar

1. Crie o arquivo de configuração:

```powershell
Copy-Item config.example.json config.json
```

2. Edite `config.json` e informe o endpoint da sua AWS IoT Core:

```json
{
  "endpoint": "xxxxxxxxxxxx-ats.iot.us-east-1.amazonaws.com"
}
```

3. Instale a dependência:

```powershell
pip install -r requirements.txt
```

4. Execute o monitor com a visualização local:

```powershell
python monitor.py --serve
```

5. Abra no navegador:

```text
http://127.0.0.1:8080/viewer/
```

## Observações

- Os certificados já estão apontados para a pasta `KEYS`.
- Se preferir, você pode sobrescrever o endpoint por variável de ambiente:

```powershell
$env:AWS_IOT_ENDPOINT="xxxxxxxxxxxx-ats.iot.us-east-1.amazonaws.com"
python monitor.py --serve
```

- O projeto usa `AmazonRootCA1.pem` por padrão. Se o seu endpoint exigir outro CA, ajuste no `config.json`.

## Ver os dados na Vercel usando sua máquina

1. Rode o monitor liberando acesso externo na sua máquina:

```powershell
python monitor.py --serve --host 0.0.0.0
```

2. Exponha a porta `8080` com um túnel público. Exemplo com Cloudflare Tunnel:

```powershell
cloudflared tunnel --url http://localhost:8080
```

Ou com ngrok:

```powershell
ngrok http 8080
```

3. Copie a URL pública gerada pelo túnel.

4. Abra a página publicada na Vercel e cole a URL no campo de conexão:

```text
https://gps-monitor-five.vercel.app
```

5. A página da Vercel vai consultar:

```text
SUA-URL-PUBLICA/api/live
```

O monitor local agora expõe esse endpoint com CORS habilitado, então a interface hospedada consegue buscar:

- status da conexão;
- última mensagem;
- histórico recente de mensagens.
