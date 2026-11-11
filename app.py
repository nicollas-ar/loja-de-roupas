import hmac
import json
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import click
import requests
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import select, update

load_dotenv()
app = Flask(__name__)
production = bool(os.getenv("RENDER"))
secret = os.getenv("SECRET_KEY")
database = os.getenv("DATABASE_URL")
if production and (not secret or not database):
    raise RuntimeError("Configure SECRET_KEY e DATABASE_URL.")
database = database or "sqlite:///loja.sqlite3"
if database.startswith(("postgres://", "postgresql://")):
    database = "postgresql+psycopg://" + database.split("://", 1)[1]
app.config.update(
    SECRET_KEY=secret or secrets.token_hex(32),
    SQLALCHEMY_DATABASE_URI=database,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    SESSION_COOKIE_SECURE=production,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=65536,
)
db = SQLAlchemy(app)
csrf = CSRFProtect(app)
catalog = json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))
sizes = ("P", "M", "G", "GG")
store = os.getenv("STORE_NAME") or "Forma Preta"
contact = os.getenv("STORE_CONTACT", "")
pickup = os.getenv("PICKUP_ADDRESS", "")
asaas_env = os.getenv("ASAAS_ENV", "sandbox")
shipping_env = os.getenv("MELHOR_ENVIO_ENV", "sandbox")
if asaas_env not in ("sandbox", "production") or shipping_env not in ("sandbox", "production"):
    raise RuntimeError("Use sandbox ou production nos ambientes de integração.")


class Order(db.Model):
    id = db.Column(db.String(48), primary_key=True)
    customer = db.Column(db.JSON, nullable=False)
    items = db.Column(db.JSON, nullable=False)
    delivery = db.Column(db.JSON, nullable=False)
    total = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(30), nullable=False, default="creating")
    payment_id = db.Column(db.String(80), unique=True)
    invoice_url = db.Column(db.String(1024))
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    payment_at = db.Column(db.DateTime)


@app.cli.command("init-db")
def init_db():
    db.create_all()


@app.cli.command("pedidos")
@click.option("--limite", default=20, type=click.IntRange(1, 100))
def orders(limite):
    for order in db.session.scalars(select(Order).order_by(Order.created_at.desc()).limit(limite)):
        click.echo(f"{order.id}  {order.status}  {money(order.total)}  {order.created_at}")


@app.cli.command("pedido")
@click.argument("codigo")
def order_detail(codigo):
    order = db.session.get(Order, codigo)
    if order is None:
        raise click.ClickException("Pedido não encontrado.")
    click.echo(json.dumps({
        "status": order.status, "cliente": order.customer, "itens": order.items,
        "entrega": order.delivery, "total": money(order.total), "cobranca": order.payment_id
    }, ensure_ascii=False, indent=2))


@app.template_filter("money")
def money(cents):
    return f"R$ {cents // 100},{cents % 100:02d}"


def digits(value):
    return re.sub(r"[^0-9]", "", value)


def cart():
    return session.get("cart", {})


def cart_rows():
    rows = []
    for key, qty in cart().items():
        sku, size = key.split(":")
        product = catalog.get(sku)
        if product and size in sizes:
            rows.append({"sku": sku, "size": size, "qty": qty, "name": product["name"],
                         "price": product["price"], "key": key})
    return rows


def subtotal(rows):
    return sum(row["price"] * row["qty"] for row in rows)


def asaas_ready():
    return bool(os.getenv("ASAAS_API_KEY") and os.getenv("ASAAS_WEBHOOK_TOKEN"))


def shipping_ready():
    return bool(os.getenv("MELHOR_ENVIO_TOKEN") and len(digits(os.getenv("ORIGIN_CEP", ""))) == 8
                and os.getenv("SHIPPING_USER_AGENT"))


def api_json(response):
    response.raise_for_status()
    return response.json(parse_float=Decimal)


def asaas(path, method="GET", payload=None):
    base = "https://api.asaas.com/v3" if asaas_env == "production" else "https://api-sandbox.asaas.com/v3"
    return api_json(requests.request(
        method, base + path, json=payload, timeout=(3, 9),
        headers={"access_token": os.environ["ASAAS_API_KEY"], "User-Agent": "FormaPreta/1.0"},
    ))


def quote(rows, cep):
    if not shipping_ready() or len(cep) != 8:
        return []
    products = []
    for row in rows:
        product = catalog[row["sku"]]
        products.append({"id": row["key"], "width": product["width"], "height": product["height"],
                         "length": product["length"], "weight": product["weight"],
                         "insurance_value": product["price"] / 100, "quantity": row["qty"]})
    base = "https://melhorenvio.com.br" if shipping_env == "production" else "https://sandbox.melhorenvio.com.br"
    response = api_json(requests.post(
        base + "/api/v2/me/shipment/calculate", timeout=(3, 9),
        headers={"Authorization": "Bearer " + os.environ["MELHOR_ENVIO_TOKEN"],
                 "User-Agent": os.environ["SHIPPING_USER_AGENT"], "Accept": "application/json"},
        json={"from": {"postal_code": digits(os.environ["ORIGIN_CEP"])},
              "to": {"postal_code": cep}, "products": products},
    ))
    if not isinstance(response, list):
        raise ValueError("Resposta de frete inválida.")
    return sorted([
        {"id": str(item["id"]), "label": item["company"]["name"] + " · " + item["name"],
         "price": int(Decimal(item.get("custom_price") or item["price"]) * 100),
         "days": item.get("custom_delivery_time") or item.get("delivery_time")}
        for item in response if not item.get("error") and item.get("price")
    ], key=lambda option: option["price"])


@app.context_processor
def globals_for_templates():
    return dict(store=store, contact=contact, whatsapp=digits(os.getenv("STORE_WHATSAPP", "")),
                count=sum(cart().values()), sandbox=asaas_env == "sandbox")


@app.get("/")
def home():
    return render_template("home.html", catalog=catalog, sizes=sizes)


@app.get("/saude")
def health():
    return {"status": "ok"}


@app.get("/loja")
def shop():
    return redirect(url_for("home", _anchor="colecao"))


@app.route("/carrinho", methods=["GET", "POST"])
def basket():
    if request.method == "POST":
        sku, size = request.form.get("sku"), request.form.get("size")
        try:
            qty = int(request.form.get("qty", "1"))
        except ValueError:
            abort(400)
        if sku not in catalog or size not in sizes or not 0 <= qty <= 10:
            abort(400)
        items = cart()
        key = f"{sku}:{size}"
        if request.form.get("action") == "add":
            items[key] = min(items.get(key, 0) + qty, 10)
            flash("Peça adicionada à sacola.")
        elif qty and request.form.get("action") != "remove":
            items[key] = qty
        else:
            items.pop(key, None)
        session["cart"] = items
        session.pop("quote", None)
        return redirect(url_for("basket"))
    rows = cart_rows()
    return render_template("cart.html", rows=rows, subtotal=subtotal(rows))


@app.route("/finalizar", methods=["GET", "POST"])
def checkout():
    rows = cart_rows()
    if not rows:
        return redirect(url_for("basket"))
    if request.method == "GET":
        session["attempt"] = secrets.token_urlsafe(24)
    form = request.form
    cep = digits(form.get("cep", ""))
    options = []
    if pickup:
        options.append({"id": "pickup", "label": "Retirada · " + pickup, "price": 0, "days": None})
    if request.method == "POST" and cep:
        try:
            options.extend(quote(rows, cep))
        except (requests.RequestException, ValueError, KeyError, TypeError):
            flash("Não foi possível cotar o frete agora. Tente novamente.")
    if request.method == "POST" and form.get("action") == "quote":
        if not options:
            flash("Nenhuma opção de entrega disponível para este CEP.")
        session["quote"] = {"cep": cep, "prices": {item["id"]: item["price"] for item in options}}
    if request.method == "POST" and form.get("action") == "pay":
        token = session.get("attempt")
        if not token or not asaas_ready():
            abort(409)
        existing = db.session.get(Order, token)
        if existing:
            return redirect(url_for("order_page", code=token))
        option = next((item for item in options if item["id"] == form.get("delivery")), None)
        previous = session.get("quote", {})
        if option is None:
            flash("Escolha uma opção de entrega disponível.")
        elif option["id"] != "pickup" and (previous.get("cep") != cep or
                previous.get("prices", {}).get(option["id"]) != option["price"]):
            flash("O frete mudou. Confira o valor atualizado e confirme novamente.")
            session["quote"] = {"cep": cep, "prices": {item["id"]: item["price"] for item in options}}
        else:
            name = form.get("name", "").strip()[:120]
            email = form.get("email", "").strip()[:150]
            cpf = digits(form.get("cpf", ""))
            phone = digits(form.get("phone", ""))
            address = {field: form.get(field, "").strip()[:120] for field in
                       ("street", "number", "complement", "district", "city", "state")}
            state = address["state"].upper()
            if not name or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(cpf) != 11 or len(phone) not in (10, 11):
                flash("Confira o nome, o e-mail, o CPF e o celular.")
            elif option["id"] != "pickup" and (len(cep) != 8 or len(state) != 2 or
                    not all(address[field] for field in ("street", "number", "district", "city"))):
                flash("Preencha o endereço de entrega completo.")
            else:
                return create_order(token, rows, option, name, email, cpf, phone,
                                    {"cep": cep, **address, "state": state})
    return render_template("checkout.html", form=form, options=options, subtotal=subtotal(rows),
                           asaas_ready=asaas_ready(), shipping_ready=shipping_ready(),
                           payment_methods={"PIX": "Pix", "CREDIT_CARD": "Cartão", "BOLETO": "Boleto"})


def create_order(token, rows, option, name, email, cpf, phone, address):
    from sqlalchemy.exc import IntegrityError

    method = request.form.get("method")
    if method not in {"PIX", "CREDIT_CARD", "BOLETO"}:
        abort(400)
    order = Order(id=token, customer={"nome": name, "email": email, "celular": phone},
                  items=rows, delivery={**option, "address": address if option["id"] != "pickup" else {}},
                  total=subtotal(rows) + option["price"])
    db.session.add(order)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return redirect(url_for("order_page", code=token))
    try:
        customer = asaas("/customers", "POST", {"name": name, "email": email, "cpfCnpj": cpf,
                         "mobilePhone": phone, "notificationDisabled": True})
        payment = asaas("/payments", "POST", {
            "customer": customer["id"], "billingType": method,
            "value": order.total / 100, "dueDate": (date.today() + timedelta(days=3)).isoformat(),
            "description": f"{store} · Pedido {token[:10]}",
            "externalReference": token,
        })
        if not payment["invoiceUrl"].startswith("https://"):
            raise ValueError("URL de cobrança inválida.")
        db.session.execute(update(Order).where(Order.id == token, Order.status.in_(("creating", "pending")))
                           .values(payment_id=payment["id"], invoice_url=payment["invoiceUrl"], status="pending"))
        db.session.commit()
        session.pop("cart", None)
        session.pop("quote", None)
    except (requests.RequestException, KeyError, TypeError, ValueError):
        app.logger.warning("Falha ao criar cobrança para pedido %s", token)
        db.session.execute(update(Order).where(Order.id == token, Order.status == "creating")
                           .values(status="payment_error"))
        db.session.commit()
    return redirect(url_for("order_page", code=token))


@app.get("/pedido/<code>")
def order_page(code):
    order = db.get_or_404(Order, code)
    return render_template("order.html", order=order)


@app.after_request
def protect_admin_responses(response):
    if request.path == "/admin" or request.path.startswith("/admin/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def admin_password():
    password = os.getenv("ADMIN_PASSWORD", "")
    if len(password) < 32:
        abort(404)
    return password


def admin_session_key(password):
    return hmac.digest(app.secret_key.encode(), password.encode(), "sha256").hex()


def admin_signed_in(password):
    expires_at = session.get("admin_expires_at", 0)
    return expires_at > time.time() and hmac.compare_digest(
        session.get("admin_session_key", ""), admin_session_key(password)
    )


@app.route("/admin", methods=["GET", "POST"])
def admin():
    password = admin_password()
    if request.method == "POST":
        submitted = request.form.get("password", "")
        if not hmac.compare_digest(password.encode(), submitted.encode()):
            return render_template("admin_login.html", invalid=True), 401
        session["admin_expires_at"] = int(time.time()) + 3600
        session["admin_session_key"] = admin_session_key(password)
        return redirect(url_for("admin"))
    if not admin_signed_in(password):
        return render_template("admin_login.html")
    code = request.args.get("pedido")
    if code:
        return render_template("admin.html", order=db.get_or_404(Order, code))
    page = db.paginate(select(Order).order_by(Order.created_at.desc(), Order.id.desc()),
                       per_page=20, max_per_page=20)
    return render_template("admin.html", page=page)


@app.post("/admin/sair")
def admin_logout():
    admin_password()
    session.pop("admin_expires_at", None)
    session.pop("admin_session_key", None)
    return redirect(url_for("admin"))


@app.get("/privacidade")
def privacy():
    return render_template("privacy.html")


@app.post("/webhooks/asaas")
@csrf.exempt
def payment_webhook():
    expected = os.getenv("ASAAS_WEBHOOK_TOKEN", "")
    received = request.headers.get("asaas-access-token", "")
    if not expected or not hmac.compare_digest(expected.encode(), received.encode()):
        abort(401)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    statuses = {"PAYMENT_CONFIRMED": "paid", "PAYMENT_RECEIVED": "paid",
                "PAYMENT_OVERDUE": "overdue", "PAYMENT_REFUNDED": "refunded",
                "PAYMENT_DELETED": "canceled", "PAYMENT_CHARGEBACK_REQUESTED": "disputed",
                "PAYMENT_PARTIALLY_REFUNDED": "partial_refund"}
    status = statuses.get(data.get("event"))
    if not status:
        return "", 204
    payment = data.get("payment")
    if not isinstance(payment, dict) or not isinstance(payment.get("id"), str):
        abort(400)
    order = db.session.scalar(select(Order).where(Order.id == payment.get("externalReference")).with_for_update())
    if order is None or (order.payment_id and order.payment_id != payment["id"]):
        return "", 204
    try:
        if Decimal(str(payment.get("value"))) * 100 != order.total:
            return "", 204
    except ArithmeticError:
        abort(400)
    transitions = {
        "paid": {"creating", "pending", "payment_error", "overdue"},
        "overdue": {"creating", "pending", "payment_error"},
        "refunded": {"creating", "pending", "payment_error", "overdue", "paid", "disputed", "partial_refund"},
        "canceled": {"creating", "pending", "payment_error", "overdue"},
        "disputed": {"creating", "pending", "payment_error", "overdue", "paid", "partial_refund"},
        "partial_refund": {"creating", "pending", "payment_error", "overdue", "paid", "disputed"},
    }
    if order.status in transitions[status]:
        order.payment_id = payment["id"]
        order.status = status
        if status == "paid":
            order.payment_at = datetime.now()
    db.session.commit()
    return "", 204
