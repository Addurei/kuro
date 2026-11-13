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

## New abilities
- [ ] "What's 15% of 240?" → 36. "5 feet in cm" → 152.4
- [ ] "Add buy cat food and finish chapter 2 to my list", "what's on my list?", "tick off the cat food"
- [ ] "Write a short poem and save it as poem.txt on my desktop": the file appears; ask again, you get poem (2).txt
- [ ] "When I say study mode, open Notepad and start a 25 minute focus", then "study mode": both happen
- [ ] "Why is my PC slow?": names real apps from Task Manager
- [ ] "Close Notepad": he asks first; Allow closes it (Notepad offers to save if you typed)
- [ ] "Tidy my downloads": the dialog shows the counts; Allow sorts them; "undo that" puts them back
- [ ] "Dark mode on" / "dark mode off". "Brightness 40" (laptop screen)
- [ ] Unplug a laptop under 20%: one warning bubble, not repeated until plugged in again

## Show panel
- [ ] "Spin a wheel: pizza, ramen, sisig" → the panel opens, the wheel spins ~3s and lands; Kuro names the same winner
- [ ] "Flip a coin" / "roll a d20" → animated flip/roll, result matches what he says
- [ ] "Chart this: Mon 3, Tue 7, Wed 5 hours" → animated bar chart; "as a pie" works too
- [ ] SPIN AGAIN replays without involving the chat

## Updates
- [ ] With update_url unset: no update traffic at all (check kuro.log)
- [ ] Publish a test release (make_release.py, serve dist/ over http), set update_url, restart → log says "update X staged"; the start after that says "updated to X" and Settings still has your key

## Looks
- [ ] He reads as a solid little figure: lit from the upper left, a cool rim on the lower right, eyes set into his face with shade at the top
- [ ] No lime line running round the chat or settings panel
- [ ] Settings → AI & Voice: one plain list of brains, the chosen one's key under it

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
