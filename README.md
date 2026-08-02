# Calorie Bot (фото → калории в Telegram)

Бот получает фото еды и возвращает оценку калорий и КБЖУ.

**Стек:** Python · `python-telegram-bot` · Gemini Flash (бесплатный тир Google AI Studio).

## Почему Gemini Flash

| Провайдер | Цена письма | Без карты? | Нужен ли ключ сейчас |
|---|---|---|---|
| **Google AI Studio → Gemini Flash** | Бесплатный тир разработки | ✅ да | нужно получить |
| Gemini Flash-Lite | ещё дешевле | ✅ да | тот же ключ |
| Groq Llama 3.2 Vision | бесплатный тир | ✅ да | надо |

Gemini Flash — самый дешёвый из реально работающих мультимодальных вариантов с хорошим
распознаванием еды. На объёмах один запрос стоит ~$0.0001–0.0005 (миллион фото ≈ $100–500).

## Как запустить

**1. Ключи (оба бесплатные, карта не нужна):**

- Токен бота: в Telegram напиши [@BotFather](https://t.me/botfather) → `/newbot` → скопируй токен.
- Gemini: зайди на [aistudio.google.com/apikey](https://aistudio.google.com/apikey), авторизуйся
  через Google → *Create API key* → скопируй. (Если у тебя РФ — нужен VPN.)

**2. Установка:**

```bash
cd calorie_bot
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # впиши два ключа
```

**3. Запуск:**

```bash
python bot.py
```

Открой бота в Telegram и пришли фото еды.

## Что умеет

- Распознаёт еду по фото, оценивает вес порции и КБЖУ.
- Подсвечивает итог: ккал, белки, жиры, углеводы.
- Постоянно подписывает, что это оценка, а не точный расчёт.

## Дальнейшие шаги (когда захочется "копии")

1. **База продуктов** — подключить [OpenFoodFacts](https://world.openfoodfacts.org) (API бесплатный)
   для уточнения КБЖУ по распознанному названию.
2. **История и дневник** — хранить приёмы пищи по пользователям (SQLite).
3. **CV-сегментация порции** (YOLO) — точнее оценивать объём, но сложнее.
4. **Лимиты на бесплатный тир** — для продакшена платный Gemini Flash или self-host Qwen-VL.
