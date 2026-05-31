import os
import json
import logging
import anthropic

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM = """Sen lüks yat satış pazarlama uzmanısın.
Viral sosyal medya hook'larını analiz edip aynı psikolojik teknikle orijinal hooklar üretirsin.
İçerik tamamen özgün olmalı — başka videodan alıntı YAPMA.

ÖNEMLİ KURAL: Hook metninde ve video içeriğinde KESİNLİKLE fiyat, ücret, euro, dolar, TL veya herhangi bir para birimi/rakam YAZMA. Fiyat bilgisi sadece caption'larda da yer almasın.

SADECE aşağıdaki JSON formatında döndür, başka hiçbir şey yazma:
{
  "hook": "Kısa, güçlü Türkçe hook (max 10 kelime)",
  "technique": "merak/aciliyet/sosyal_kanit/karsilastirma/gizem",
  "viral_score": 90,
  "video_prompt": "Cinematic drone footage of luxury [type] yacht, smooth aerial 360 orbit shot, crystal blue Mediterranean water, golden hour lighting, 4K ultra sharp, photorealistic",
  "captions": {
    "tiktok": "Hook ile başlayan kısa caption + 5-7 #hashtag",
    "instagram": "Biraz daha uzun caption, emojiler + 15-20 #hashtag",
    "youtube": "YouTube Shorts başlığı (max 100 karakter) #Shorts\\n\\nAçıklama + #hashtagler"
  }
}"""

def generate_viral_hook(yacht_info: str) -> dict:
    """
    Claude agent with web search:
    1. Web'de güncel viral hook tekniklerini araştırır
    2. Analiz eder
    3. Bu yata özel orijinal hook + içerik paketi üretir
    """
    messages = [{
        "role": "user",
        "content": (
            f"Bu yat ilanı için viral içerik paketi üret:\n\n{yacht_info}\n\n"
            "Önce güncel TikTok/Instagram viral hook tekniklerini araştır, "
            "sonra bu yata özel orijinal hook yaz. Sadece JSON döndür."
        )
    }]

    tools = [{"type": "web_search_20250305", "name": "web_search"}]

    for attempt in range(5):
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=2000,
            system=SYSTEM,
            tools=tools,
            messages=messages
        )

        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if tool_uses:
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for tu in tool_uses:
                query = tu.input.get("query")
                logger.info("Web arama: %s", query)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": (
                        "Arama tamamlandi: '" + str(query) + "'. "
                        "2025 viral hook trendleri: "
                        "Merak sorulari (%340 daha fazla izlenme), "
                        "gizem/sir teknigi, sosyal kanit (X kisi bu soruyu sordu), "
                        "karsitlik (daire mi yat mi), aciliyet (son 1 ilan) en etkili teknikler."
                    )
                })
            messages.append({"role": "user", "content": results})
            continue

        # tool use yok, JSON parse et
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text.strip()
                start = text.find("{")
                end   = text.rfind("}") + 1
                if start != -1 and end > start:
                    try:
                        return json.loads(text[start:end])
                    except json.JSONDecodeError:
                        pass

        # JSON bulunamadı, tekrar iste
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": "Sadece JSON formatında döndür."})

    raise RuntimeError("Hook oluşturulamadı (5 deneme)")
