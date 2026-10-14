# Macro Desk — US অর্থনীতি ও মার্কেট ডেটা

EdgeFinder-এর "Economic Data" অংশটার মতো একটা ডেস্ক — বাঁ দিকে একই মেনু, ডান দিকে একই
ধরনের চার্ট (বার = actual, লাল লাইন = forecast, সবুজ ড্যাশ = target), নিচে "About this graph"।
শুধু **সংখ্যা আর খবর** — কোনো signal, entry, SL/TP নেই।

**লাইভ:** https://jahanara1101.github.io/macro-desk/

## সাইডবারে কী কী

**Sentiment & Positioning**
- COT Report — CFTC, গোল্ড/সিলভার/WTI/E-mini S&P-এর net positioning (৬০ সপ্তাহ)
- COT Index & Velocity — ৫২ সপ্তাহের range-এ index + ৪/১৩/২৬ সপ্তাহের velocity
- Retail Sentiment — FastBull resting orders, buy বনাম sell
- Put/Call Ratio — CBOE দৈনিক (total / index / equity / VIX)
- Net Options Volume — call − put, প্রোডাক্ট অনুযায়ী
- Put & Call Walls — call/put open interest

**Macro**
- Macro Scanners — ১০ দেশের HIGH-importance ইভেন্ট
- Economic Heatmap — কোন দিকে অর্থনীতি গরম/ঠান্ডা
- Market Heatmap — ২৬টা অ্যাসেট (ধাতু/জ্বালানি/FX/সূচক/ক্রিপ্টো/ইল্ড), এক নজরে
- Momentum & Volatility — 1W/1M/3M/YTD + Volatility.D + ATR + technical rating
- Seasonality — প্রতি মাসের গড় রিটার্ন + কত % বছর পজিটিভ
- Score History — সোনার সাপ্তাহিক −১৫…+১৫ স্কোর (৫১ সপ্তাহ)
- Top Setups — real yield + dollar + COT crowding দিয়ে আজকের স্কোর
- Economic Calendar — সামনের সব US ইভেন্ট

**Economic Data** — Growth (GDP, retail sales, ISM, durable goods…), Inflation
(CPI/Core CPI/PPI/PCE — YoY ও MoM), Labor (NFP, ADP, unemployment, claims),
Interest Rates & Yields (Fed + FRED line series), Housing & Consumer

Country dropdown ১০টা দেশ, Range dropdown শেষ ১২/২৪/৩৬ রিলিজ বা সব।

## ডেটা সোর্স (সব keyless, কোনো API key নেই)

| সোর্স | কী দেয় |
|---|---|
| TradingView economic calendar | রিলিজ-ভিত্তিক actual / forecast / previous (`Origin`+`Referer` হেডার লাগে) |
| TradingView scanner | ২৬টা অ্যাসেটের দাম / % change / volatility / rating (এক POST) |
| FRED (St. Louis Fed) | ধারাবাহিক লেভেল সিরিজ (rates, yields, dollar, housing) + মাসিক সিজনালিটি |
| CFTC public reporting | COT positioning (commodities `72hh-3qpy`, index futures `gpe5-46if`) |
| CBOE daily market statistics | put/call ratio, call/put volume + open interest |
| FastBull pending orders | রিটেইল resting buy/sell order |

## ফাইল

```
scripts/fetch_macro.py   — সব সোর্স টানে, data/*.json লেখে
scripts/update.sh        — fetch + commit + push (বদলালে তবেই commit)
scripts/check_page.js    — হেডলেস চেক: পেজের নিজের JS আসল ডেটায় চালিয়ে ৪৯টা পেজ যাচাই
data/cal_<cc>.json       — দেশভিত্তিক ইভেন্ট (~৩ বছর পিছনে + ১২০ দিন সামনে)
data/series.json         — ১৪টা FRED সিরিজ
data/cot.json            — COT history
data/options.json        — CBOE daily
data/walls.json          — FastBull resting orders
data/meta.json           — কখন বানানো, কোন সোর্স কাজ করেছে
index.html               — পুরো সাইট (vanilla JS + SVG, কোনো CDN নেই)
```

## নিজে চালাতে

```bash
python3 scripts/fetch_macro.py     # ~১১ সেকেন্ড
node scripts/check_page.js         # সব পেজ ঠিক থাকলে "ALL PAGES RENDER OK"
node scripts/check_page.js /root/macro-desk --dump=cpi   # একটা পেজের লেখা দেখো
bash scripts/update.sh             # fetch + publish
```

## যে ফাঁদগুলোতে ধাক্কা খেয়ে ঠিক করা হয়েছে

1. **FRED-কে browser User-Agent পাঠানো যাবে না।** Chrome UA দিলে FRED ০ byte ফেরত দেয় আর
   curl timeout পর্যন্ত ঝুলে থাকে (একবার ৪ মিনিট ২৭ সেকেন্ড লাগেছিল)। curl-এর ডিফল্ট UA-তে
   সব সোর্স তৎক্ষণাৎ সাড়া দেয়।
2. **FRED HTTP/2 মাঝপথে কেটে দেয়** (`curl rc=92`) ⇒ `--http1.1` বাধ্যতামূলক।
3. **CBOE পেজ একটা Next.js app** — সংখ্যাগুলো `self.__next_f.push([1,"…"])` RSC flight
   stream-এ থাকে এবং **একটা অবজেক্ট মাঝখানে কেটে দুই চাঙ্কে ভাগ করা থাকে** (একবার `prevTradin`
   / পরের চাঙ্কে `gDay":"2026-10-13"`)। তাই আগে সব চাঙ্ক জোড়া লাগাও, তারপর পার্স করো।
   শুধু `"` খুঁজে কাটলে ভুল জায়গায় কাটা পড়ে।
4. **CFTC-র market name URL-encode করতে হবে `quote(safe="")` দিয়ে** — `S&P 500`-এর `&`
   না এনকোড করলে Socrata query ভেঙে 400 দেয়; আর `order=… DESC`-এর space-ও `%20` হতে হবে।
5. **CRUDE OIL, LIGHT SWEET (NYMEX) সিরিজটা ২০২২-০২-এ শেষ** — লাইভ WTI কনট্র্যাক্ট এই
   ডেটাসেটে `WTI FINANCIAL CRUDE OIL - NEW YORK MERCANTILE EXCHANGE`।
6. **YoY কখনো রো-ইনডেক্স দিয়ে নয়** — সিরিজে মাস বাদ পড়তে পারে (`CPIAUCNS`-এ 2025-10 গায়েব,
   shutdown-এর ফাঁক), তখন `rows[i-12]` মানে ১২ মাস নয় ⇒ CPI ৩.৪০%-এর জায়গায় ৩.৬৯% আসে।
   তারিখ মিলিয়ে (`(year-1, same month)`) হিসাব করো।
7. **সিরিজ বাছাই:** headline CPI = **NSA** (`CPIAUCNS`/`CPILFENS`)। PPI headline = `PPIFIS`
   (Final Demand); `PPIACO` ৯.৮৫% দেখায়, headline ৫.৪১%।

## সীমা

- প্রতি ১৫ মিনিটে cron রিফ্রেশ করে, **রিয়েল-টাইম নয়** — release-এর পর actual বসতে দেরি হয়।
- FRED-এ প্রকাশ-বিলম্ব আছে (CPI মাসের মাঝে, GDP ত্রৈমাসিক)।
- সোনার **অপশন** wall পাওয়া যায় না (CME, ফ্রি রিয়েল-টাইম নেই) — সোনার resting order
  wall দেখতে Retail Sentiment।
- **AAII Sentiment** ফ্রি সোর্স নেই, তাই সাইডবারে রাখা হয়নি।
- এটা **তথ্য**, বিনিয়োগ পরামর্শ নয়। এখানে কোনো trade setup নেই।
