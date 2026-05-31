# Yat Viral Ajan — Kurulum Rehberi

Telegram'dan yat fotoğrafı + bilgisi gönderirsin → sistem viral hook bulur →
ElevenLabs ile seslendirir → Runway ile drone videosu üretir →
FFmpeg ile birleştirir → TikTok / Instagram / YouTube'a otomatik yayınlar.

---

## Adım 1 — Ön gereksinimler

```bash
# Python 3.11+ ve FFmpeg olmalı
python --version
ffmpeg -version

# Yoksa kur (Mac)
brew install python ffmpeg

# Yoksa kur (Ubuntu)
apt-get install python3.11 ffmpeg
```

---

## Adım 2 — Proje kurulumu

```bash
git clone <bu-repo>
cd yacht-agent
pip install -r requirements.txt
cp .env.example .env
```

---

## Adım 3 — API hesapları (sırayla aç)

### Telegram Bot
1. Telegram'da @BotFather'a yaz → `/newbot`
2. İsim ver → token'ı kopyala → `.env`'e yaz
3. Bota `/start` yaz → chat_id'ni öğren:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
4. YOUR_CHAT_ID'ye yaz

### Anthropic
1. console.anthropic.com → API Keys → Create Key
2. ANTHROPIC_API_KEY'e yaz

### ElevenLabs
1. elevenlabs.io → Sign Up → Profile → API Keys
2. ELEVENLABS_API_KEY'e yaz
3. Voice Library'den Türkçe ses seç → ID'yi ELEVENLABS_VOICE_ID'ye yaz

### Runway ML
1. app.runwayml.com → Settings → API Keys → Create
2. RUNWAY_API_KEY'e yaz
3. Not: Gen-3 Alpha Turbo kullanılıyor (~$0.05/saniye)

### Cloudinary (Instagram için gerekli)
1. cloudinary.com → Free hesap aç
2. Dashboard'dan cloud_name, api_key, api_secret al → .env'e yaz

### Instagram Graph API
1. developers.facebook.com → Create App → Business
2. Instagram Graph API ekle
3. Instagram Business hesabını bağla
4. Graph API Explorer → uzun ömürlü token üret (60 gün)
5. INSTAGRAM_ACCESS_TOKEN ve INSTAGRAM_USER_ID'ye yaz

### TikTok Developer
1. developers.tiktok.com → Create App
2. Content Posting API iznini ekle
3. OAuth ile hesabı bağla → access token al
4. TIKTOK_ACCESS_TOKEN'a yaz

### YouTube Data API
1. console.cloud.google.com → New Project
2. YouTube Data API v3 etkinleştir
3. OAuth 2.0 credentials oluştur
4. python setup_youtube_auth.py ile token al (aşağıda)
5. Tüm YOUTUBE_ değişkenlerini doldur

---

## Adım 4 — YouTube OAuth (ilk kez)

```python
# setup_youtube_auth.py dosyası (proje klasöründe çalıştır)
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json",
    scopes=["https://www.googleapis.com/auth/youtube.upload"]
)
creds = flow.run_local_server(port=8080)
print("ACCESS TOKEN:", creds.token)
print("REFRESH TOKEN:", creds.refresh_token)
```

---

## Adım 5 — Webhook kur ve başlat

```bash
# .env doldurulduktan sonra

# Lokal test (ngrok ile)
ngrok http 8000
# Çıkan URL'yi al: https://xxxx.ngrok.io

# Webhook'u Telegram'a kaydet
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://xxxx.ngrok.io/webhook/yacht2025"

# Uygulamayı başlat
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Adım 6 — Deploy (Railway.app — önerilen)

```bash
# Railway CLI kur
npm install -g @railway/cli

# Login ve deploy
railway login
railway init
railway up

# Ortam değişkenlerini ekle
railway variables set TELEGRAM_BOT_TOKEN=xxx ...

# Domain al, webhook'u güncelle
railway domain
# Çıkan domain ile webhook'u tekrar kaydet
```

---

## Kullanım

Telegram'da bota fotoğraf gönder, caption'a şunu yaz:
```
Model: Azimut 55
Fiyat: €1.200.000
Konum: Bodrum, Türkiye
Uzunluk: 16.8m
Kabin: 4
Özellikler: flybridge, geniş güverte, şef mutfağı, stabilizatör
```

Sistem otomatik olarak:
1. Viral hook araştırır ve üretir
2. ElevenLabs ile seslendirir
3. Runway ile 10 saniyelik drone videosu üretir
4. FFmpeg ile hook (3 sn) + drone (10 sn) birleştirir
5. TikTok, Instagram Reels, YouTube Shorts'a yükler
6. Telegram'a "yayınlandı + linkler" mesajı atar

---

## Maliyet (video başına)

| Servis      | Maliyet       |
|-------------|---------------|
| Claude API  | ~$0.01        |
| ElevenLabs  | ~$0.10        |
| Runway Gen-3| ~$0.50        |
| Cloudinary  | Ücretsiz tier |
| **Toplam**  | **~$0.61**    |

---

## Sorun giderme

```bash
# Logları gör (Docker)
docker logs yacht-agent -f

# FFmpeg testi
ffmpeg -version

# Telegram webhook kontrol
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```
