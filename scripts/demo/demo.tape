Output out/demo.mp4

Set Shell "zsh"
Set FontFamily "Menlo"
Set FontSize 26
Set LineHeight 1.25
Set Width 1920
Set Height 1080
Set Padding 40
Set Margin 44
Set MarginFill "#1a1a1a"
Set WindowBar Colorful
Set BorderRadius 12
Set TypingSpeed 55ms
Set CursorBlink false
Set Theme { "name": "keyfit", "background": "#111111", "foreground": "#e5e5e5", "cursor": "#fafafa", "selection": "#333333", "black": "#262626", "red": "#f87171", "green": "#4ade80", "yellow": "#facc15", "blue": "#60a5fa", "magenta": "#c084fc", "cyan": "#22d3ee", "white": "#d4d4d4", "brightBlack": "#8a8a8a", "brightRed": "#fca5a5", "brightGreen": "#86efac", "brightYellow": "#fde047", "brightBlue": "#93c5fd", "brightMagenta": "#d8b4fe", "brightCyan": "#67e8f9", "brightWhite": "#fafafa" }

Hide
Type "source ./env.zsh && clear"
Enter
Sleep 1s
Show

# Each step waits for what it needs on screen (Wait), then holds it long enough to read (Sleep).

# 1. harbor-api: Claude starts with harbor's accounts and sees only harbor's Linear
Sleep 700ms
Type "cd ~/code/harbor-api"
Enter
Sleep 300ms
Type "claude"
Enter
Wait+Screen@20s /keyfit · harbor-api/
Sleep 3s
Screenshot out/1-harbor.png
Type "/mcp"
Sleep 500ms
Enter
Wait+Screen@10s /linear_harbor/
Sleep 3s
Screenshot out/2-harbor-mcp.png
Escape
Sleep 500ms
Type "/exit"
Sleep 500ms
Enter
Wait@15s /harbor-api \$/
Sleep 600ms

# 2. lumen-web: the AWS sign-in expired, Claude does not start; sign in, then it starts
Type "cd ../lumen-web"
Enter
Wait /lumen-web \$/
Sleep 300ms
Type "claude"
Enter
Wait+Screen@20s /Sign in now/
Sleep 3.5s
Screenshot out/3-refused.png
Type "y"
Sleep 300ms
Enter
Wait+Screen@15s /starting claude/
Sleep 800ms
Screenshot out/4-signed-in.png
Wait+Screen@20s /keyfit · lumen-web/
Sleep 3s
Screenshot out/5-lumen.png

# 3. lumen-web sees only lumen's Linear
Type "/mcp"
Sleep 500ms
Enter
Wait+Screen@10s /linear_lumen/
Sleep 5s
Screenshot out/6-lumen-mcp.png
