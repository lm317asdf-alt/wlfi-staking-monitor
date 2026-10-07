#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WLFI Staking Monitor - v8 (无兜底，穿透代理读真实值)
"""

import urllib.request
import json
import csv
import os
import time
from datetime import datetime, UTC

# ==================== 配置 ====================
RPC = "https://mainnet.infura.io/v3/ce3adbd0b7344c2d935f323c61ebc7b0"
STAKING = "0x196feE96efeAAd585483A6d950e70286B6851923"
IMPLEMENTATION = "0xF757d6805eC4cB6f6c9c4c8e8e8e8e8e8e8e8e8e"  # ← 填完整实现合约地址
WLFI = "0xda5e1988097297dcdc1f90d4dfe7909e847cbef6"
USD1 = "0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d"
COINGECKO_ID = "world-liberty-financial"

INITIAL_POOL_USD1 = 1_250_000
POOL_DURATION_DAYS = 180
PROGRAM_START = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)

CSV_FILE = "staking_history.csv"
HTML_FILE = "wlfi_dashboard.html"
SLEEP_BETWEEN_REQUESTS = 1.5
# =============================================


def rpc_call(method, params, retries=3):
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(RPC, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if i == retries - 1:
                return {"error": str(e)}
            time.sleep(2)
    return {"error": "max retries"}


def http_get(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(SLEEP_BETWEEN_REQUESTS)
    return None


def read_balance(token, owner):
    """balanceOf(owner) — 直接读"""
    pad = "0" * 24 + owner[2:]
    data = "0x70a08231" + pad
    r = rpc_call("eth_call", [{"to": token, "data": data}, "latest"])
    result = r.get("result", "0x")
    if result and result != "0x":
        try:
            return int(result, 16) / 1e18
        except Exception:
            return 0.0
    return 0.0


def read_balance_raw(token, owner):
    """balanceOf 返回原始 uint256，不除小数位"""
    pad = "0" * 24 + owner[2:]
    data = "0x70a08231" + pad
    r = rpc_call("eth_call", [{"to": token, "data": data}, "latest"])
    result = r.get("result", "0x")
    if result and result != "0x":
        try:
            return int(result, 16)
        except Exception:
            return 0
    return 0


def read_balance_through_proxy(token, owner):
    """
    穿透代理：先读代理，若为0且配置了实现合约地址，再读实现合约
    """
    # 先试代理
    val = read_balance(token, owner)
    if val > 0:
        return val, "balanceOf(代理)"

    # 代理返回0，尝试实现合约
    if IMPLEMENTATION and IMPLEMENTATION != "0x":
        val2 = read_balance(token, IMPLEMENTATION)
        if val2 > 0:
            return val2, "balanceOf(实现合约)"

        # 还不行，试 raw（不除18位小数，USD1 可能是6位或0位）
        raw = read_balance_raw(token, owner)
        if raw > 0:
            # 试不同小数位
            for dec in [6, 8, 0, 18]:
                candidate = raw / (10 ** dec)
                if candidate > 0 and candidate < 1e12:  # 合理范围
                    return candidate, f"balanceOf(raw/10^{dec})"

    return 0.0, "0 (无法读取)"


def read_usd1_pool_real(usd1_addr, staking_addr):
    """
    专门读 USD1 奖励池：先试代理，再试实现合约，再试 raw 不同小数位
    """
    # 方法1: 标准 balanceOf 代理
    raw = read_balance_raw(usd1_addr, staking_addr)
    if raw > 0:
        # USD1 可能是 18, 6, 8, 0 位小数
        for dec in [18, 6, 8, 0]:
            val = raw / (10 ** dec)
            if 1000 < val < 2_000_000:  # 奖励池应该在 125万左右
                return val, f"balanceOf(代理, /10^{dec})"

    # 方法2: 实现合约
    if IMPLEMENTATION and IMPLEMENTATION != "0x":
        raw2 = read_balance_raw(usd1_addr, IMPLEMENTATION)
        if raw2 > 0:
            for dec in [18, 6, 8, 0]:
                val = raw2 / (10 ** dec)
                if 1000 < val < 2_000_000:
                    return val, f"balanceOf(实现合约, /10^{dec})"

    # 方法3: 直接读 staking 合约的 USD1 余额（Etherscan 方式）
    # 用 eth_getStorageAt 碰运气？不，还是 balanceOf
    return 0.0, "0 (无法读取)"


def get_block():
    r = rpc_call("eth_blockNumber", [])
    try:
        return int(r.get("result", "0x0"), 16)
    except Exception:
        return 0


def get_wlfi_price(history):
    """多源兜底"""
    try:
        d = http_get("https://api.binance.com/api/v3/ticker/price?symbol=WLFIUSDT")
        if d and "price" in d:
            return float(d["price"]), "Binance"
    except Exception:
        pass

    time.sleep(SLEEP_BETWEEN_REQUESTS)

    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={COINGECKO_ID}&vs_currencies=usd"
        data = http_get(url)
        if data:
            p = data.get(COINGECKO_ID, {}).get("usd")
            if p:
                return float(p), "CoinGecko"
    except Exception:
        pass

    time.sleep(SLEEP_BETWEEN_REQUESTS)

    try:
        url = (f"https://api.coingecko.com/api/v3/simple/token_price/ethereum"
               f"?contract_addresses={WLFI.lower()}&vs_currencies=usd")
        data = http_get(url)
        if data:
            k = WLFI.lower()
            p = data.get(k, {}).get("usd")
            if p:
                return float(p), "CoinGecko(合约)"
    except Exception:
        pass

    time.sleep(SLEEP_BETWEEN_REQUESTS)

    try:
        d = http_get("https://owlchart.com/api/wlfi/price")
        if d and "priceUsd" in d:
            return float(d["priceUsd"]), "OwlChart"
    except Exception:
        pass

    for row in reversed(history):
        try:
            p = float(row.get("wlfi_price_usd", 0))
            if p > 0:
                return p, "历史缓存"
        except Exception:
            continue

    return 0.0, "none"


def calc_auto_apy(total_staked, price):
    if total_staked <= 0 or price <= 0:
        return 0.0
    daily_release = INITIAL_POOL_USD1 / POOL_DURATION_DAYS
    yearly_reward = daily_release * 365
    staked_usd = total_staked * price
    return (yearly_reward / staked_usd) * 100


def load_history():
    history = []
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                history.append(row)
    return history


def append_snapshot(timestamp, block, total_staked, price, apy, usd1_pool):
    usd_value = total_staked * price if price > 0 else 0
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "timestamp", "block", "total_staked_wlfi", "wlfi_price_usd",
                "staked_usd_value", "apy_estimate", "usd1_reward_pool"
            ])
        writer.writerow([
            timestamp, block, f"{total_staked:.6f}", f"{price:.6f}",
            f"{usd_value:.2f}", f"{apy:.4f}",
            f"{usd1_pool:.0f}"
        ])
    return usd_value


def line_chart(values, labels, color, unit=""):
    W, H = 700, 270
    pl, pr, pt, pb = 74, 20, 16, 48
    iw, ih = W - pl - pr, H - pt - pb
    n = len(values)

    if n < 2:
        return (f'<svg viewBox="0 0 {W} {H}" class="chart">'
                f'<rect width="{W}" height="{H}" fill="#131829"/>'
                f'<text x="{W//2}" y="{H//2}" fill="#5a6a8a" text-anchor="middle" font-size="13">'
                f'运行 2 次以上才会出现曲线</text></svg>')

    vmax = max(values) * 1.15 or 1
    vmin = 0.0

    def xy(i, v):
        x = pl + iw * i / (n - 1)
        y = pt + ih * (1 - (v - vmin) / (vmax - vmin))
        return x, y

    pts = [xy(i, values[i]) for i in range(n)]
    gid = color.lstrip("#")
    s = [f'<defs><linearGradient id="g{gid}" x1="0" y1="0" x2="0" y2="1">'
         f'<stop offset="0%" stop-color="{color}" stop-opacity="0.32"/>'
         f'<stop offset="100%" stop-color="{color}" stop-opacity="0.02"/></linearGradient></defs>']

    for k in range(4):
        y = pt + ih * k / 3
        val = vmax * (1 - k / 3)
        if unit == "$":
            txt = f"${val:,.2f}" if val < 10 else f"${val:,.0f}"
        elif unit == "%":
            txt = f"{val:.1f}%"
        else:
            txt = f"{val/1e6:.1f}M" if val >= 1e6 else (f"{val/1e3:.1f}K" if val >= 1e3 else f"{val:.0f}")
        s.append(f'<line x1="{pl}" y1="{y:.0f}" x2="{W-pr}" y2="{y:.0f}" stroke="#1e2945" stroke-width="1"/>')
        s.append(f'<text x="{pl-10}" y="{y+4:.0f}" fill="#5a6a8a" font-size="10" text-anchor="end">{txt}</text>')

    poly = " ".join(f"{x:.0f},{y:.0f}" for x, y in pts)
    s.append(f'<polygon points="{pl},{pt+ih} {poly} {pts[-1][0]:.0f},{pt+ih}" fill="url(#g{gid})"/>')
    s.append(f'<polyline points="{" ".join(f"{x:.0f},{y:.0f}" for x,y in pts)}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>')
    for i in range(0, n, max(1, n // 8)):
        s.append(f'<circle cx="{pts[i][0]:.0f}" cy="{pts[i][1]:.0f}" r="4" fill="#0a0e27" stroke="{color}" stroke-width="2.5"/>')
    for i in range(0, n, max(1, n // 6)):
        s.append(f'<text x="{pts[i][0]:.0f}" y="{pt+ih+30}" fill="#5a6a8a" font-size="10" text-anchor="middle">{labels[i]}</text>')

    return f'<svg viewBox="0 0 {W} {H}" class="chart" preserveAspectRatio="xMidYMid meet">{"".join(s)}</svg>'


def generate_html(history, s):
    if not history:
        now_short = datetime.now(UTC).strftime("%m-%d %H:%M")
        history = [{
            "timestamp": now_short, "total_staked_wlfi": f"{s['staked']:.6f}",
            "wlfi_price_usd": f"{s['price']:.6f}",
            "staked_usd_value": f"{s['staked']*s['price']:.2f}",
            "apy_estimate": f"{s['apy']:.2f}",
            "usd1_reward_pool": f"{s['usd1_pool']:.0f}",
        }]

    labels, staked_data, price_data, apy_data, pool_data = [], [], [], [], []
    for row in history:
        ts = row.get("timestamp", "")
        if len(ts) > 10:
            ts = ts[5:16]
        labels.append(ts)
        for key, lst in [("total_staked_wlfi", staked_data), ("wlfi_price_usd", price_data),
                         ("apy_estimate", apy_data), ("usd1_reward_pool", pool_data)]:
            try:
                lst.append(float(row.get(key, 0)))
            except Exception:
                lst.append(0)

    if staked_data:
        first_staked = staked_data[0]; latest_staked = staked_data[-1]
        change_pct = ((latest_staked - first_staked) / first_staked * 100) if first_staked > 0 else 0
    else:
        latest_staked = 0; change_pct = 0

    charts = {
        "staked": line_chart(staked_data, labels, "#667eea", unit=""),
        "price": line_chart(price_data, labels, "#f59e0b", unit="$"),
        "apy": line_chart(apy_data, labels, "#4ade80", unit="%"),
        "pool": line_chart(pool_data, labels, "#a78bfa", unit=""),
    }

    arrow = "▲" if change_pct >= 0 else "▼"
    arrow_cls = "up" if change_pct >= 0 else "down"
    price_cls = "" if s["price"] > 0 else "warn"
    price_disp = f"${s['price']:.6f}" if s['price'] > 0 else "暂不可用"
    price_note = f"来源: {s['price_src']}" if s['price_src'] != "none" else "使用历史缓存"

    updated = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>WLFI Staking Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0a0e27;color:#e0e6ff;padding:16px;min-height:100vh}}
.container{{max-width:640px;margin:0 auto}}
h1{{font-size:22px;text-align:center;background:linear-gradient(135deg,#667eea,#764ba2);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.sub{{text-align:center;font-size:12px;color:#8892b0;margin:4px 0 20px}}
.card{{background:#131829;border:1px solid #1e2945;border-radius:16px;padding:20px;margin-bottom:16px}}
.ct{{font-size:14px;color:#8892b0;margin-bottom:8px}}
.cv{{font-size:28px;font-weight:700}}
.cc{{font-size:13px;margin-top:6px}}
.up{{color:#4ade80}} .down{{color:#f87171}} .warn{{color:#fbbf24}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}}
.box{{background:#131829;border:1px solid #1e2945;border-radius:12px;padding:14px;text-align:center}}
.bl{{font-size:11px;color:#8892b0}}
.bv{{font-size:18px;font-weight:600;margin-top:4px}}
.chart{{width:100%;height:auto;display:block}}
.badge{{display:inline-block;background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;font-weight:700;padding:4px 12px;border-radius:20px;font-size:14px}}
.src{{font-size:10px;color:#5a6a8a;margin-top:6px;line-height:1.4}}
.foot{{text-align:center;font-size:11px;color:#5a6a8a;margin-top:16px;line-height:1.6}}
</style></head>
<body>
<div class="container">
<h1>🔒 WLFI Staking</h1>
<p class="sub">链上实时质押监控 · {len(history)} 个数据点</p>
<div class="card">
<div class="ct">当前总质押量</div>
<div class="cv">{latest_staked:,.0f} <span style="font-size:14px;color:#8892b0">WLFI</span></div>
<div class="cc {arrow_cls}">{arrow} 累计变化 {change_pct:+.2f}%</div>
</div>
<div class="grid">
<div class="box"><div class="bl">WLFI 价格</div><div class="bv {price_cls}">{price_disp}</div><div class="src">{price_note}</div></div>
<div class="box"><div class="bl">质押价值</div><div class="bv">${s['staked']*s['price']:,.0f}</div></div>
<div class="box"><div class="bl">推算 APY</div><div class="bv"><span class="badge">{s['apy']:.1f}%</span></div></div>
<div class="box"><div class="bl">奖励池 USD1</div><div class="bv" style="font-size:15px">{s['usd1_pool']:,.0f}</div></div>
</div>
<div class="card"><div class="ct">📈 质押量趋势</div>{charts['staked']}</div>
<div class="card"><div class="ct">💰 WLFI 价格趋势</div>{charts['price']}</div>
<div class="card"><div class="ct">📊 推算 APY 趋势</div>{charts['apy']}</div>
<div class="card"><div class="ct">🪙 奖励池 USD1 余额</div>{charts['pool']}</div>
<div class="foot">最后更新: {updated} UTC<br>APY 根据 1.25M/180天 线性释放模型自动推算<br>
奖励池 = 合约内 USD1 实时余额（穿透代理读取）</div>
</div>
</body></html>"""

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"📊 HTML 图表已生成: {HTML_FILE}")


def main():
    print("=" * 50)
    print("  WLFI Staking Monitor - v8")
    print("=" * 50)

    now = datetime.now(UTC)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    print("\n📡 读取链上数据...")
    block = get_block()

    # WLFI 质押量 — 穿透代理
    print("   🔍 读取 WLFI 质押量（穿透代理）...")
    total_staked, stake_src = read_balance_through_proxy(WLFI, STAKING)
    print(f"   🔒 总质押量: {total_staked:,.6f} WLFI")
    print(f"      来源: {stake_src}")

    print(f"   🧱 区块: {block}")

    # USD1 奖励池 — 专门穿透
    print("\n🪙 读取奖励池 USD1 余额（穿透代理）...")
    usd1_pool, pool_src = read_usd1_pool_real(USD1, STAKING)
    print(f"   💰 奖励池 USD1: {usd1_pool:,.0f} USD1")
    print(f"      来源: {pool_src}")

    print("\n💰 拉取 WLFI 价格 (多源)...")
    history = load_history()
    price, price_src = get_wlfi_price(history)
    if price > 0:
        print(f"   ✅ 来源: {price_src}")
        print(f"   WLFI/USD: ${price:.6f}")
    else:
        print(f"   ⚠️ 价格暂不可用")

    print("\n🧮 自动推算 APY...")
    elapsed_days = max(0, (now - PROGRAM_START).days)
    elapsed_days = min(elapsed_days, POOL_DURATION_DAYS)
    daily_release = INITIAL_POOL_USD1 / POOL_DURATION_DAYS
    print(f"   已运行: {elapsed_days} 天")
    print(f"   日均释放: {daily_release:,.2f} USD1/天")

    apy = calc_auto_apy(total_staked, price)
    print(f"   📊 推算 APY: {apy:.2f}%")

    state = {
        "staked": total_staked, "price": price, "price_src": price_src,
        "apy": apy, "usd1_pool": usd1_pool,
    }

    usd_val = append_snapshot(now_str, block, total_staked, price, apy, usd1_pool)
    print(f"\n✅ 已写入 {CSV_FILE}")

    print(f"\n📊 生成图表...")
    generate_html(load_history(), state)

    print(f"\n{'=' * 50}")
    print(f"  📋 摘要")
    print(f"{'=' * 50}")
    print(f"  时间: {now_str} UTC")
    print(f"  总质押: {total_staked:,.6f} WLFI ({stake_src})")
    if price > 0:
        print(f"  价格: ${price:.6f}")
        print(f"  价值: ${usd_val:,.2f}")
    print(f"  推算APY: {apy:.2f}%")
    print(f"  奖励池USD1: {usd1_pool:,.0f} ({pool_src})")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
