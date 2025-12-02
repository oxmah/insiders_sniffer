# 🕵️‍♂️ insiders_sniffer

List **buyer addresses** for a given token on **Solana (SOL)** within a **specified time range**.

## ✨ Features
- Extracts buyer wallet addresses for a specific token
- Filters results over a custom time window (time range)
- Lightweight script you can run locally

## 📦 Requirements
- Python 3.9+ recommended
- `requests` library

## ⚡ Installation
- `pip install requests`

## 🚀 Usage
- `python insiders_sniffer.py`
- `$env:HELIUS_KEY="YOUR_FREE_HELIUS_API_KEY"` for Windows (Powershell)
- `set "YOUR_FREE_HELIUS_API_KEY"` for Windows (CMD)
- `export HELIUS_KEY="YOUR_FREE_HELIUS_API_KEY"` for Ubuntu

## 🧠 Notes
- Configure the **token** and **time range** inside the script (or via CLI args if you implemented them).
- Easy upgrades: export CSV/JSON, dedupe addresses, rank by buy count/volume.
- We leverage https://www.helius.dev/ to query data.

## 📄 License
MIT
