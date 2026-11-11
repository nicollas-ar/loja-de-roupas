# Forma Preta

Loja Flask de moda fitness feminina. O catálogo inclui as três fotos fornecidas e aceita pedidos sem conta de cliente. A sacola usa uma sessão assinada, os pedidos ficam no banco e o pagamento acontece na página do Asaas.

## 1. Preparar o catálogo e subir ao GitHub

- Extraia o ZIP e envie os arquivos e pastas extraídos para a **raiz** de um repositório GitHub. Envie também `.gitignore` e `.env.example`. Não envie o próprio ZIP nem um arquivo `.env`.
- Edite `catalog.json` para definir preços, descrição, dimensões em centímetros e peso em quilogramas. O preço é em centavos. Os valores fornecidos são exemplos. Confirme também os tamanhos oferecidos em `app.py`.
- Defina nome e canais de contato nas variáveis de ambiente. Antes de abrir a loja, inclua sua identificação comercial, tabela de medidas, prazos de preparo, condições de troca e devolução e política de privacidade revisada. `templates/privacy.html` descreve o fluxo de dados implementado.
- Controle a disponibilidade das peças manualmente. A loja não reserva nem baixa estoque e não emite etiquetas de frete automaticamente.

## 2. Criar o banco no Neon

1. Crie um projeto PostgreSQL e abra **Connect**.
2. Selecione o banco e a função, habilite **Connection pooling** e copie a conexão PostgreSQL completa com SSL.
3. No Render, informe essa conexão como `DATABASE_URL`. Ela contém a senha do banco. Para este primeiro deploy, o Flask cria a tabela de pedidos na inicialização.

[Conectar um aplicativo ao Neon](https://neon.com/docs/connect/connect-from-any-app)

## 3. Publicar no Render

Em **New → Web Service**, conecte o repositório e escolha o runtime Python. Se preferir o fluxo **Blueprint**, o arquivo `render.yaml` já declara o serviço e gera `SECRET_KEY`.

| Campo | Valor |
| --- | --- |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `flask --app app init-db && gunicorn --bind 0.0.0.0:$PORT --timeout 60 app:app` |
| Health Check Path | `/saude` |

Em **Environment**, configure `DATABASE_URL` e, para deploy manual, `SECRET_KEY`. Gere a chave localmente com `python -c "import secrets; print(secrets.token_hex(32))"`. Após o deploy, abra a URL da loja e confirme que `/saude` retorna `{"status":"ok"}`.

[Publicar Flask no Render](https://render.com/docs/deploy-flask)

## 4. Configurar o Asaas

1. Gere uma chave na conta Asaas Sandbox e salve como `ASAAS_API_KEY`. Defina `ASAAS_ENV=sandbox`.
2. Gere outro segredo para `ASAAS_WEBHOOK_TOKEN` e configure o mesmo valor no campo de autenticação do webhook no Asaas. O endpoint é `https://SEU-SERVICO.onrender.com/webhooks/asaas`.
3. Assine os eventos `PAYMENT_CONFIRMED`, `PAYMENT_RECEIVED`, `PAYMENT_OVERDUE`, `PAYMENT_REFUNDED`, `PAYMENT_DELETED`, `PAYMENT_CHARGEBACK_REQUESTED` e `PAYMENT_PARTIALLY_REFUNDED`.
4. Configure retirada ou frete e faça um pedido com os dados de teste do Asaas. Abra a cobrança, simule o pagamento e verifique a mudança de status na página do pedido. Confirme os meios de pagamento habilitados na sua conta.
5. Para operar, troque `ASAAS_ENV` para `production`, cadastre o webhook na conta de produção e informe a chave e o token correspondentes. Use um banco ou branch Neon separado do teste se quiser manter o histórico comercial limpo.

[Sandbox Asaas](https://docs.asaas.com/docs/sandbox) · [Eventos do webhook](https://docs.asaas.com/docs/receba-eventos-do-asaas-no-seu-endpoint-de-webhook)

## 5. Configurar entrega

Para oferecer retirada, preencha `PICKUP_ADDRESS` com o endereço. Para cotar frete, gere um token com acesso à cotação no Melhor Envio e configure:

| Variável | Valor |
| --- | --- |
| `MELHOR_ENVIO_ENV` | `sandbox` ou `production`, conforme o token |
| `MELHOR_ENVIO_TOKEN` | Token do Melhor Envio |
| `ORIGIN_CEP` | CEP de postagem |
| `SHIPPING_USER_AGENT` | Nome da integração e seu e-mail de contato, como `Forma Preta (loja@exemplo.com)` |

Use dimensões e peso da embalagem reais e compare a cotação com uma simulação na conta Melhor Envio. O checkout inclui o frete no total e registra o endereço do pedido. Após confirmar o pagamento, emita a etiqueta pelo painel do Melhor Envio e envie o código de rastreamento à cliente pelo canal de atendimento.

[Documentação do Melhor Envio](https://docs.melhorenvio.com.br/)

## 6. Consultar pedidos

Em um terminal com o projeto instalado e `DATABASE_URL` apontando para o Neon da loja:

```sh
flask --app app pedidos --limite 20
flask --app app pedido CODIGO_DO_PEDIDO
```

O primeiro comando lista pedidos e o segundo mostra itens, dados de entrega e a referência do Asaas. Se uma criação de cobrança falhar ou ficar pendente, procure o código do pedido no Asaas pelo campo de referência externa antes de pedir à cliente que repita a compra. Use o status do Asaas para distinguir pagamento confirmado de saldo já recebido.

## Rodar localmente

Use Python 3.12 ou 3.13.

```sh
python -m venv .venv
```

No PowerShell, ative com `.\.venv\Scripts\Activate.ps1`. No Linux ou macOS, use `source .venv/bin/activate`.

```sh
python -m pip install -r requirements.txt
flask --app app init-db
flask --app app run
```

Abra http://127.0.0.1:5000. Para configurar integrações, copie `.env.example` para `.env` e preencha os campos localmente. Se `DATABASE_URL` estiver vazia, será usado SQLite. Em `STORE_WHATSAPP`, informe país, DDD e número, como `5511999999999`.

## Painel administrativo

No Render, abra **Environment** e adicione `ADMIN_PASSWORD`. Gere uma senha aleatória com `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Guarde-a em um gerenciador de senhas. No ambiente local, use a mesma variável no seu `.env`.

Acesse digitando `https://SEU-SERVICO.onrender.com/admin` na barra de endereços. A loja pública não exibe link para essa rota. Sem uma senha configurada de pelo menos 32 caracteres, o painel retorna 404. O painel permite consultar pedidos e seus dados de entrega. A sessão administrativa dura uma hora.
