# Macro Desk — US অর্থনীতির খবর

একটা সাইট যেখানে শুধু **US macro / economic news** থাকে — CPI, PPI, PCE, NFP, GDP, বেকারত্ব, Fed rate, yield, ডলার ইনডেক্স। EdgeFinder-এর "Economic Data" অংশটার মতো, কিন্তু শুধু নিউজ আর ডেটা — কোনো signal, entry, SL/TP নেই।

**লাইভ:** https://jahanara1101.github.io/macro-desk/

## কী দেখায়

- **পরের বড় রিলিজ** — countdown সহ (বাংলা সময়)
- **মূল সংখ্যা** — ১৪টা headline series, স্পার্কলাইন সহ
- **ক্যালেন্ডার** — actual বনাম forecast, HOT / COOL / ON TARGET ব্যাজ

## ডেটা সোর্স (দুটোই keyless, কোনো API key লাগে না)

| সোর্স | কী দেয় | এন্ডপয়েন্ট |
|---|---|---|
| TradingView economic calendar | US ইভেন্টের actual / forecast / previous | `economic-calendar.tradingview.com/events?from=…&to=…&countries=US` |
| FRED (St. Louis Fed) | সিরিজের ইতিহাস (লেভেল) | `fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>` |

## ফাইল

```
scripts/fetch_macro.py   — ডেটা টানে, data/*.json লেখে
scripts/update.sh        — fetch + git commit + push (ডেটা বদলালে তবেই commit)
scripts/check_page.js    — হেডলেস চেক: পেজের নিজের JS আসল ডেটায় চালিয়ে যাচাই
data/calendar.json       — US ইভেন্ট (past 45d + next 45d)
data/indicators.json     — ১৪টা series, ১৮০ পয়েন্ট ইতিহাস
data/meta.json           — কখন বানানো, কোন সোর্স কাজ করেছে
index.html               — পুরো সাইট (vanilla JS, কোনো CDN নেই)
```

## নিজে চালাতে

```bash
python3 scripts/fetch_macro.py     # ~১ সেকেন্ড
node scripts/check_page.js         # সব চেক পাস করলে OK
bash scripts/update.sh             # fetch + publish
```

## যেসব ফাঁদে ধাক্কা খেয়ে ঠিক করা হয়েছে

1. **FRED-কে browser User-Agent পাঠানো যাবে না।** Chrome UA দিলে FRED ০ byte ফেরত দেয় আর curl timeout পর্যন্ত ঝুলে থাকে। curl-এর ডিফল্ট UA-তে দুটো সোর্সই তৎক্ষণাৎ সাড়া দেয়।
2. **FRED HTTP/2 মাঝপথে কেটে দেয়** (`curl rc=92`), তাই `--http1.1` বাধ্যতামূলক।
3. **YoY কখনো রো-ইনডেক্স দিয়ে হিসাব করা যাবে না** — সিরিজে মাস বাদ পড়তে পারে (`CPIAUCNS`-এ 2025-10 গায়েব, সরকারি shutdown-এর ফাঁক), তখন `rows[i-12]` মানে ১২ মাস নয় ⇒ CPI 3.40%-এর জায়গায় 3.69% আসে। তারিখ মিলিয়ে (`(year-1, same month)`) হিসাব করো।
4. **সঠিক সিরিজ বাছাই জরুরি:** headline CPI = **NSA** (`CPIAUCNS`, `CPILFENS`), SA সিরিজ (`CPIAUCSL`) ভিন্ন সংখ্যা দেয়। PPI headline = **`PPIFIS`** (Final Demand); `PPIACO` (All Commodities) ৯.৮৫% দেখায়, headline ৫.৪১%।

## সীমা

- সাইট **প্রতিদিন** রিফ্রেশ হয় (cron), রিয়েল-টাইম নয় — release-এর পর actual বসতে কয়েক মিনিট লাগতে পারে।
- FRED সিরিজে প্রকাশ-বিলম্ব আছে (CPI সাধারণত মাসের মাঝে, GDP quarterly)।
- এটা **তথ্য**, বিনিয়োগ পরামর্শ নয়। এখানে কোনো trade setup নেই।
