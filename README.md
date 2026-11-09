# Kuro 🐈‍⬛

A little black cat with small magical powers who lives on your Windows desktop, talks with you, and actually does things on your PC. You pick his brain: Gemini, Groq or OpenRouter (free keys), Ollama (free, offline), or OpenAI / Claude (paid).

## Quick start

1. Install **Python 3.12** (python.org, tick "Add python.exe to PATH").
2. Double-click **`setup.bat`** once.
3. Double-click **`run.bat`** → right-click Kuro → ⚙ Settings → **AI & Voice**.
4. Pick a brain, paste its key (the "Get a key ↗" link takes you there) and press **Test**. Changes save by themselves.

| Brain | Cost | Notes |
|---|---|---|
| Gemini | free key | Best all-rounder; can see your screen and images |
| Groq | free key | Very fast replies |
| OpenRouter | free key | Kuro only uses its free models |
| Ollama | free, offline | Runs on your PC (run `setup_offline_brain.bat` once; ~16 GB RAM recommended) |
| OpenAI / Claude | paid key | Strongest; both can see images |

Kuro picks the best model each key can use; type one in Settings → Model to override it.
If Test fails, it shows the provider's exact reason (bad key, no internet, limit reached…). Errors are also logged to `%APPDATA%\Kuro\kuro.log`.

## Using him

| Do this | To |
|---|---|
| Double-click Kuro / `Ctrl+Alt+K` | Open or close the chat |
| `Ctrl+Alt+Space` | Talk (push-to-talk) |
| Say "Hey Kuro…" | Hands-free (turn on in Settings → Voice) |
| Select text anywhere + `Ctrl+Alt+Q` | Quick actions: summarize · explain · rewrite · fix · translate |
| Click | Pet him (shows the timer while you focus) |
| Right-click | Radial menu: chat · talk · focus · memory · settings · quit |
| Drag / throw | Pick him up; he stays where he lands |
| Esc | Close the HUD |

## What he can do

- **Chat in character**, typed out in his HUD and spoken aloud. Chat size: Settings → General.
- **Control your PC**: apps, sites, files, folders, windows, volume, media, screenshots, clipboard, lock. Power actions and PowerShell always ask first.
  Apps are found by name (Start menu, Store apps, installed programs), and he waits for the window to actually appear before saying it's open. If it doesn't, he tells you.
- **Scan** (just ask): a document, PDF, Word file, code, an image, copied or pasted text, the song playing, your screen, or your PC.
- **Study mode**: "make a quiz from biology.pdf" or "flashcards from these notes". Opens in the Study panel. Every question is checked (4 distinct options, one valid answer) before it's shown.
- **Focus companion**: right-click → FOCUS (or "focus for 50 minutes"). Pomodoro cycles; he naps on the taskbar with a timer ring, wakes you for breaks, cheers at the end, and keeps daily stats.
- **Daily briefing**: once a day, the first time he sees you: weather (he finds your city from your connection, nothing to type), today's calendar, reminders, unread mail, yesterday's focus. Or ask "brief me".
- **Google Calendar + Gmail** (optional): see and add events (adding asks first), read your inbox. Gmail is read-only: he can never send, delete or change mail.
- **Reminders** that survive restarts.
- **Learns about you** (Right-click → Memory to review or forget).
- **Teleports** around your screens now and then (5 styles), and **hides during fullscreen** games and videos, holding any messages until you're back.

### Connecting Google (one-time, ~5 minutes)

1. Go to console.cloud.google.com → create a project.
2. APIs & Services → Library: enable **Google Calendar API** and **Gmail API**.
3. OAuth consent screen: External, add your app name, and add yourself as a **test user**.
4. Credentials → Create credentials → OAuth client ID → **Desktop app**. Copy the client ID and secret.
5. Kuro → Settings → Google: paste both, press CONNECT, sign in in the browser.

The sign-in token is stored encrypted for your Windows account (DPAPI) in `%APPDATA%\Kuro\google.token`.
**Selling note:** Gmail read access is a Google "restricted" scope. Until the app passes Google's verification (which includes a security assessment), only the test users you add (up to 100) can connect. Calendar only needs the lighter "sensitive" verification.

## The HUD

There's only one panel, and it's pinned above Kuro's head. It follows him wherever he goes and reshapes itself between the speech bubble, chat, settings, memory and the "allow this?" prompt. A lime light runs around its border: faster while he's thinking, red when he needs your permission.

## How he moves

Kuro is drawn fresh every frame (60 fps) from a skeleton, not a flipbook:

- **Body**: a flexible spine from hips to chest that stretches and gathers when he gallops, with tapered tubes for flesh.
- **Legs**: shoulder → elbow → paw and hip → knee → paw, solved with two-bone IK, so paws plant and joints bend naturally. Walk and gallop are real gait patterns, and his paws advance with distance travelled, so they never slide.
- **2.5D**: the skeleton is projected for his current angle, so he turns smoothly from facing you to side-on.
- **Springs everywhere**: every pose value glides with a little overshoot. Speed changes kick the head (it lags), ears (they flop) and tail (it drags).

| File | Role |
|---|---|
| `app/ui/cat_rig.py` | The skeleton, IK, gaits and drawing |
| `app/ui/cat.py` | Behaviour, teleports, physics, sparks |
| `app/ui/quick.py` | Quick-actions panel |
| `app/features.py` | Focus, study, briefing, Google, quick actions, wake word, do-not-disturb |
| `app/focus.py` · `app/study.py` · `app/briefing.py` · `app/google.py` | The logic behind them |
| `app/ui/hud.py` | The pinned HUD (all pages) and the radial menu |
| `app/ui/theme.py` | Matte look, running border light, icons |
| `app/providers.py` | All six brains: format translation, automatic model choice, fallbacks, error reasons |
| `app/launcher.py` | Finding, opening and confirming apps |
| `app/location.py` | Automatic city + weather (Open-Meteo, free, no key) |
| `app/brain.py` | Tool-use loop, persona, history |
| `app/tools.py` | Every PC action. **Add new abilities here** |
| `app/memory.py` | Facts, habits, background reflection |
| `app/controller.py` | Wires it all together |

## Tests

`python -m pytest` runs the test suite (115 tests): tools, scans, memory, the tool loop, every brain against mocked servers, app launching, focus cycles, quiz validation, the briefing, and Google sign-in/Calendar/Gmail against mocked endpoints.

## Known limits

- Free brains have rate limits. When he hits one, he says so and tries the next model.
- Web answers (news, scores) use Google Search with Gemini, and a DuckDuckGo search with the other brains.
- Voice recognition (and the wake word) uses Google's free web speech service, so it needs internet. The wake word only sends short phrases that sound like speech.
- On Windows, clicks pass through the transparent area around the HUD. This hasn't been verified on real hardware yet.

## Before selling Kuro

Never ship your own API key inside the app; anyone can extract it. Either users paste their own free key (how it works now), or you run a small server that holds the key and checks subscriptions. Also: write a short privacy policy, since he reads files and remembers personal facts. A code-signing certificate avoids Windows SmartScreen warnings. Check Google's Gemini API terms for commercial use.
