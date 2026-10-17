"""SmartServe AI backend (FastAPI on Vercel).
NOTE: data is held in memory and seeded for demo. Vercel functions are
stateless, so for persistence swap STATE/BATCHES for Neon/Supabase Postgres."""
import os, base64, datetime as dt
import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel

app = FastAPI(title="SmartServe AI")

PRODUCTS = {
    "p1": dict(id="p1", name="Fresh Milk 1L", category="Dairy", price=62, cost=48, demand=14, barcode="8901262150019"),
    "p2": dict(id="p2", name="Croissant (pack of 4)", category="Bakery", price=120, cost=70, demand=9, barcode="8906001230045"),
    "p3": dict(id="p3", name="Paneer 200g", category="Dairy", price=95, cost=72, demand=7, barcode="8901058881236"),
    "p4": dict(id="p4", name="Whole Wheat Bread", category="Bakery", price=50, cost=34, demand=12, barcode="8902080012233"),
    "p5": dict(id="p5", name="Cold Coffee Mix", category="Beverage", price=210, cost=150, demand=4, barcode="8904004400011"),
}
BATCHES = [
    dict(id=1, pid="p1", qty=30, exp=1), dict(id=2, pid="p1", qty=24, exp=6),
    dict(id=3, pid="p2", qty=16, exp=0), dict(id=4, pid="p2", qty=20, exp=2),
    dict(id=5, pid="p3", qty=12, exp=3), dict(id=6, pid="p4", qty=20, exp=4),
    dict(id=7, pid="p4", qty=14, exp=1), dict(id=8, pid="p5", qty=25, exp=80),
]
STATE = dict(day=0, nid=9, waste=0.0, recovered=0.0, sold=0)
WEEK = [.9, .85, .9, .95, 1.1, 1.35, 1.3]  # Sun..Sat demand multipliers
START = dt.date.today()


def d_of(off): return START + dt.timedelta(days=off)
def wk(off): return (d_of(off).weekday() + 1) % 7
def forecast_day(p, off): return round(p["demand"] * WEEK[wk(off)])
def stock(pid): return sum(b["qty"] for b in BATCHES if b["pid"] == pid and b["exp"] >= STATE["day"])


def suggest(b):
    left, p = b["exp"] - STATE["day"], PRODUCTS[b["pid"]]
    if left < 0: return None
    pct = 50 if left == 0 else 35 if left == 1 else 20 if left <= 3 else 0
    if not pct: return None
    floor = -(-p["cost"] * 105 // 100)                      # never below cost + 5%
    price = max(floor, round(p["price"] * (1 - pct / 100)))
    why = "expires today" if left == 0 else "expires tomorrow" if left == 1 else f"expires in {left} days"
    return dict(pct=round((1 - price / p["price"]) * 100), price=price, reason=why, capped=price == floor)


def view(b):
    left = b["exp"] - STATE["day"]
    return {**b, "product": PRODUCTS[b["pid"]]["name"], "expiry": d_of(b["exp"]).isoformat(), "days_left": left,
            "status": "expired" if left < 0 else "soon" if left <= 2 else "fresh", "suggestion": suggest(b)}


def alerts():
    out = []
    for b in sorted(BATCHES, key=lambda x: x["exp"]):
        left, p = b["exp"] - STATE["day"], PRODUCTS[b["pid"]]
        if left < 0: out.append(dict(type="expired", text=f"{p['name']} ({b['qty']}) expired. Remove from shelf."))
        elif left <= 3: out.append(dict(type="soon", text=f"{p['name']} ({b['qty']}) expires in {left}d, batch #{b['id']}"))
    for p in PRODUCTS.values():
        need = sum(forecast_day(p, STATE["day"] + i) for i in (1, 2, 3))
        if stock(p["id"]) < need:
            out.append(dict(type="low", text=f"{p['name']} low: {stock(p['id'])} left vs ~{need} needed in 3 days"))
    return out


@app.get("/api/overview")
def overview():
    risk = [b for b in BATCHES if 0 <= b["exp"] - STATE["day"] <= 3]
    return dict(date=d_of(STATE["day"]).isoformat(), products=list(PRODUCTS.values()),
                batches=[view(b) for b in sorted(BATCHES, key=lambda x: x["exp"])],
                alerts=alerts(), stats=dict(waste=round(STATE["waste"]), recovered=round(STATE["recovered"]),
                sold=STATE["sold"], at_risk=sum(b["qty"] * PRODUCTS[b["pid"]]["price"] for b in risk)))


@app.get("/api/forecast/{pid}")
def forecast(pid: str):
    p = PRODUCTS.get(pid) or (_ for _ in ()).throw(HTTPException(404, "Unknown product"))
    return dict(product=p["name"], stock=stock(pid), days=[
        dict(date=d_of(STATE["day"] + i).isoformat(), demand=forecast_day(p, STATE["day"] + i)) for i in range(7)])


class NewBatch(BaseModel):
    product_id: str
    qty: int
    expiry: str  # YYYY-MM-DD


@app.post("/api/batches")
def add_batch(nb: NewBatch):
    if nb.product_id not in PRODUCTS: raise HTTPException(404, "Unknown product")
    off = (dt.date.fromisoformat(nb.expiry) - START).days
    b = dict(id=STATE["nid"], pid=nb.product_id, qty=max(1, nb.qty), exp=off); STATE["nid"] += 1
    BATCHES.append(b)
    return dict(batch=view(b), reminders=[d_of(off - 3).isoformat(), d_of(off - 1).isoformat()])


class NewProduct(BaseModel):
    name: str; category: str = "General"; price: float; cost: float; barcode: str = ""


@app.post("/api/products")
def add_product(np: NewProduct):
    pid = f"p{len(PRODUCTS) + 1}"
    PRODUCTS[pid] = dict(id=pid, name=np.name, category=np.category, price=np.price, cost=np.cost, demand=6, barcode=np.barcode)
    return PRODUCTS[pid]


def find(bid):
    return next((b for b in BATCHES if b["id"] == bid), None) or (_ for _ in ()).throw(HTTPException(404, "No batch"))


@app.post("/api/batches/{bid}/discount")
def apply_discount(bid: int):
    b, s = find(bid), suggest(find(bid))
    if not s: raise HTTPException(400, "No safe discount available")
    b["disc"], b["dprice"] = s["pct"], s["price"]
    return view(b)


@app.post("/api/batches/{bid}/writeoff")
def writeoff(bid: int):
    b = find(bid); STATE["waste"] += b["qty"] * PRODUCTS[b["pid"]]["cost"]; BATCHES.remove(b)
    return dict(ok=True)


@app.post("/api/advance")
def advance():
    """Demo 'time travel': move one day, sell FIFO, expire stock."""
    STATE["day"] += 1
    for p in PRODUCTS.values():
        dem = forecast_day(p, STATE["day"])
        for b in sorted([b for b in BATCHES if b["pid"] == p["id"] and b["exp"] >= STATE["day"]], key=lambda x: x["exp"]):
            if dem <= 0: break
            t = min(b["qty"], round(dem * (1.6 if b.get("disc") else 1)))
            b["qty"] -= t; dem -= t; STATE["sold"] += t
            if b.get("disc"): STATE["recovered"] += t * b["dprice"]
    for b in BATCHES:
        if b["exp"] < STATE["day"] and b["qty"] > 0 and not b.get("w"):
            b["w"] = True; STATE["waste"] += b["qty"] * PRODUCTS[b["pid"]]["cost"]
    BATCHES[:] = [b for b in BATCHES if b["qty"] > 0]
    return overview()


@app.get("/api/barcode/{code}")
async def barcode(code: str):
    local = next((p for p in PRODUCTS.values() if p["barcode"] == code), None)
    if local: return dict(found=True, source="local", product=local)
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            j = (await c.get(f"https://world.openfoodfacts.org/api/v2/product/{code}.json")).json()
        if j.get("status") == 1:
            pr = j["product"]
            return dict(found=True, source="openfoodfacts", product=dict(
                name=pr.get("product_name") or "Unknown", category=(pr.get("categories") or "General").split(",")[0], barcode=code))
    except Exception:
        pass
    return dict(found=False)


@app.post("/api/ocr")
async def ocr(file: UploadFile = File(...)):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key: return dict(date=None, raw="", note="Set ANTHROPIC_API_KEY to enable OCR")
    img = base64.b64encode(await file.read()).decode()
    body = dict(model="claude-haiku-5-5", max_tokens=30, messages=[dict(role="user", content=[
        dict(type="image", source=dict(type="base64", media_type=file.content_type or "image/jpeg", data=img)),
        dict(type="text", text=f"Read the expiry / best-before date on this package. Today is {START}. "
             "Reply with only the date as YYYY-MM-DD, or NONE if unreadable.")])])
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.anthropic.com/v1/messages", json=body,
                         headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    txt = r.json()["content"][0]["text"].strip()
    try: dt.date.fromisoformat(txt); return dict(date=txt, raw=txt)
    except ValueError: return dict(date=None, raw=txt)
