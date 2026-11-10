# Kuro real-use test pass

Run on a real Windows PC after `run.bat` (first start installs the new voice packages).
Tick each line; note anything odd with the time it happened, then share
`%APPDATA%\Kuro\kuro.log` and `%APPDATA%\Kuro\crash.log`.

## Start-up
- [ ] Kuro appears, no spoken greeting
- [ ] Brand-new profile (rename `%APPDATA%\Kuro` temporarily): welcome flow, name, Groq key, tips
- [ ] Log contains `music watcher on`

## Brain
- [ ] Groq: ask "what can you do?" — words stream in, speech starts after the first sentence
- [ ] Gemini, OpenRouter, Ollama (if installed): Settings → AI & Voice → Test, then one question each
- [ ] Backup brain: with Gemini out of quota and a Groq key added, a reply still comes and the chat notes the switch
- [ ] Bad key: Test explains the problem

## Voice
- [ ] Natural voice (Amy) speaks after ~1 min of first use (download); Ryan / Lessac / Windows switch live
- [ ] Cute voice on/off is audible
- [ ] Ctrl+Alt+Space: English, Filipino and Taglish sentences are transcribed correctly
- [ ] "Hey Kuro" (if enabled) wakes him

## PC control
- [ ] "open Spotify", "open Blender", "open unity hub", "open calculator": the app really opens
- [ ] "open notepad" while Notepad is open: brought to front
- [ ] "open zzzz": he says he couldn't find it
- [ ] Volume set to 30%, screenshot, play a song on YouTube

## Music / DJ mode
- [ ] YouTube song in the active tab: miniplayer + headphones within ~1 s
- [ ] Hover sidebar videos 10 s: miniplayer stays on your song
- [ ] Documentary / ad / Short: no DJ mode
- [ ] Spotify song: DJ mode within ~3 s

## Activity
- [ ] VS Code: glasses on, typing makes his paws tap
- [ ] Word / Google Docs: pencil; Blender: beret
- [ ] At most one comment per 15 min; none during focus or fullscreen

## Petting & play
- [ ] Rub the cursor back and forth over his head (no clicking): he purrs, squeezes his eyes shut, leans into your hand, hearts float up; stops when you stop
- [ ] Do the same while he sleeps: he purrs and smiles but stays asleep
- [ ] Click his nose: eyes cross, then sparkle. Click his belly: giggle and a small hop
- [ ] Click him 7 times fast: still gets grumpy
- [ ] With "Speak replies" on (natural voice): his mouth opens and closes in time with the words, and shuts between sentences
- [ ] Leave the mouse alone 5+ minutes, then move it: a happy hop to see you back
- [ ] After 11 pm: sleepy half-closed eyes, a yawn now and then
- [ ] Empty chat: three suggestion buttons; tapping one sends it
- [ ] Right-click menu: icons swell under the cursor, fold away when you pick one

## Focus & sleep
- [ ] Right-click → FOCUS: he goes to the taskbar, dozes off (heavy eyes, then closed), nose bubble + blep
- [ ] Break and end of session messages

## Settings
- [ ] Size slider: he grows/shrinks live, feet stay put
- [ ] Rebind Chat to Ctrl+Alt+J: works immediately; Ctrl+C is refused
- [ ] Language → Filipino: his lines and replies switch

## Screens
- [ ] 150% / 200% scaling, a second monitor, taskbar on the side
- [ ] Fullscreen game/video: he hides, comes back after

## Stability
- [ ] Leave him running 2+ hours: CPU stays low when idle (Task Manager)
- [ ] Kill `python.exe` (the child) in Task Manager: he comes back with "Oops, I tripped…"
