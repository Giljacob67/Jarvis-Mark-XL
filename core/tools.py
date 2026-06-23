"""
MARK XL — Canonical tool schema (single source of truth).

This module is THE definition of every tool the assistant LLM can call. It used
to live inline in main.py; the planner (agent/planner.py) maintained its own,
separate copy of the same tool list, so the two could silently drift apart.

Everything that needs the tool set imports it from here:
  * main.py            -> OLLAMA_TOOLS (schema sent to the LLM at runtime)
  * agent/planner.py   -> TOOL_NAMES   (validated against the planner's own list)

Declarations use the original Gemini-style type names ("STRING", "OBJECT", ...);
`_to_ollama_tools()` converts them to the OpenAI/Ollama JSON-schema format.
"""

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens or launches any application, website, or program on the computer. "
            "ALWAYS use this when the user says: open, launch, start, run, pull up, "
            "or 'open X real quick'. Examples: 'open WhatsApp', 'open Chrome', "
            "'launch Spotify', 'open calculator', 'pull up WhatsApp'. "
            "Do NOT use send_message just because the app is a messaging app — "
            "if the user only says to open it, call open_app."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {"type": "STRING", "description": "Name of the application or website to open"}
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {"city": {"type": "STRING", "description": "City name"}},
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": (
            "Sends a message to a specific person via WhatsApp, Telegram, or similar. "
            "ONLY use this when the user explicitly provides BOTH a recipient AND message content. "
            "Example triggers: 'text John saying I am late', 'send a WhatsApp to mom that dinner is ready'. "
            "Do NOT call this if the user only wants to open the app without sending a message."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The exact message text to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' or 'camera'. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description"},
                "value":       {"type": "STRING", "description": "Optional value"}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "chrome | edge | firefox | opera | operagx | brave | vivaldi | safari"},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query"},
                "engine":      {"type": "STRING", "description": "google | bing | duckduckgo | yandex"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels"},
                "key":         {"type": "STRING", "description": "Key name for press"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto"},
                "description": {"type": "STRING", "description": "What the code should do"},
                "language":    {"type": "STRING", "description": "Programming language"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both"},
                "game_name": {"type": "STRING",  "description": "Game name"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when done"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis."
        ),
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "file_processor",
        "description": (
            "Processes any file that the user has uploaded or dropped onto the interface. "
            "Supports: images, PDFs, Word docs, CSV/Excel, JSON, code files, audio, video, archives."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path":   {"type": "STRING",  "description": "Full path to the uploaded file"},
                "action":      {"type": "STRING",  "description": "What to do with the file"},
                "instruction": {"type": "STRING",  "description": "Free-form instruction"},
                "format":      {"type": "STRING",  "description": "Target format for conversion"},
                "width":       {"type": "INTEGER", "description": "Target width for image resize"},
                "height":      {"type": "INTEGER", "description": "Target height for image resize"},
                "scale":       {"type": "NUMBER",  "description": "Scale factor"},
                "quality":     {"type": "INTEGER", "description": "Quality 1-100"},
                "start":       {"type": "STRING",  "description": "Start time for trim"},
                "end":         {"type": "STRING",  "description": "End time for trim"},
                "timestamp":   {"type": "STRING",  "description": "Timestamp for video frame extraction"},
                "column":      {"type": "STRING",  "description": "Column name for CSV filter/sort"},
                "value":       {"type": "STRING",  "description": "Filter value"},
                "condition":   {"type": "STRING",  "description": "Filter condition"},
                "ascending":   {"type": "BOOLEAN", "description": "Sort order"},
                "save":        {"type": "BOOLEAN", "description": "Save result to file"},
                "destination": {"type": "STRING",  "description": "Output folder for archive extract"},
            },
            "required": []
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save a personal fact about the user to permanent long-term memory. "
            "MANDATORY: call this IMMEDIATELY (without asking) whenever the user states or corrects: "
            "their name, age, city, job, school, language, nationality, a preference, a goal, or a relationship. "
            "Examples: "
            "'my name is Fatih' → (identity, name, Fatih) | "
            "'not Travis, Fatih' → (identity, name, Fatih) | "
            "'I am 22' → (identity, age, 22) | "
            "'I live in Ankara' → (identity, city, Ankara) | "
            "'I prefer dark mode' → (preferences, ui_theme, dark mode). "
            "Call SILENTLY alongside your verbal reply — never announce that you are saving."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity (name/age/city/job/school/nationality) | "
                        "preferences (likes/dislikes/habits) | "
                        "projects (active work/goals) | "
                        "relationships (people in their life) | "
                        "wishes (future plans/wants) | "
                        "notes (anything else)"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key, e.g. 'name', 'age', 'favorite_color'"},
                "value": {"type": "STRING", "description": "Concise value in English"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "email_tool",
        "description": (
            "Sends or reads emails. Use for: sending an email to someone, "
            "checking recent emails, reading inbox."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "send | read"},
                "to":      {"type": "STRING", "description": "Recipient email address (for send)"},
                "subject": {"type": "STRING", "description": "Email subject (for send)"},
                "body":    {"type": "STRING", "description": "Email body text (for send)"},
                "folder":  {"type": "STRING", "description": "Mail folder (for read, default: INBOX)"},
                "limit":   {"type": "INTEGER", "description": "Number of emails to read (default: 5)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "spotify",
        "description": (
            "Controls Spotify playback. Use for: playing music, pausing, "
            "skipping tracks, checking what's playing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | pause | resume | next | previous | status"},
                "query":  {"type": "STRING", "description": "Song/artist/album to play"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "notes",
        "description": (
            "Manage personal notes. Use for: saving quick notes, listing notes, "
            "searching through notes, deleting notes."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "add | list | search | delete | clear"},
                "text":   {"type": "STRING", "description": "Note text (for add)"},
                "query":  {"type": "STRING", "description": "Search query (for search)"},
                "id":     {"type": "INTEGER", "description": "Note ID (for delete)"},
                "limit":  {"type": "INTEGER", "description": "Max results (for list/search)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "translator",
        "description": (
            "Translates text between languages. Use for: translating phrases, "
            "detecting language of text."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":          {"type": "STRING", "description": "translate | detect"},
                "text":            {"type": "STRING", "description": "Text to translate or detect"},
                "target_language": {"type": "STRING", "description": "Target language (for translate, default: English)"},
                "source_language": {"type": "STRING", "description": "Source language (for translate, default: auto)"},
            },
            "required": ["action", "text"]
        }
    },
    {
        "name": "timer",
        "description": (
            "Set countdown timers. Use for: setting reminders, cooking timers, "
            "pomodoro timers. Speaks when done."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "start | list | cancel"},
                "seconds": {"type": "INTEGER", "description": "Duration in seconds (for start)"},
                "label":   {"type": "STRING", "description": "Timer label (for start)"},
                "id":      {"type": "INTEGER", "description": "Timer ID (for cancel)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "calculator",
        "description": (
            "Calculates mathematical expressions. Use for: math calculations, "
            "conversions, unit math. Supports basic ops and math functions."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "expression": {"type": "STRING", "description": "Math expression to evaluate"},
            },
            "required": ["expression"]
        }
    },
    {
        "name": "enroll_voice",
        "description": (
            "Enrolls the user's voice for speaker verification. "
            "Records audio for the specified duration and creates a voice print. "
            "Use when user says 'enroll my voice', 'learn my voice', 'voice enrollment'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "duration": {"type": "INTEGER", "description": "Recording duration in seconds (default: 10)"},
            },
            "required": []
        }
    },
    {
        "name": "vector_search",
        "description": (
            "Semantic search over long-term memories using vector embeddings. "
            "Use for finding related memories, facts, or patterns without exact keywords."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Search query"},
                "category": {"type": "STRING", "description": "Optional category filter"},
                "limit": {"type": "INTEGER", "description": "Max results (default: 5)"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "trigger_distillation",
        "description": (
            "Manually triggers pattern distillation from recent conversations. "
            "Extracts recurring topics, preferences, and learned facts."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "remote_control",
        "description": (
            "Manages the encrypted web dashboard for remote phone control. "
            "Actions: 'start' (start the dashboard server), 'stop' (stop it), "
            "'status' (check if running), 'url' (get connection URL), "
            "'qr' (generate QR code pairing link for phone), "
            "'new_key' (generate a new 6-char pairing key)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Action: start, stop, status, url, qr, new_key"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "phone_mic",
        "description": (
            "Controls phone microphone streaming. Actions: "
            "'status' (check if phone mic is active), "
            "'stop' (stop phone mic stream), "
            "'queue_size' (check how many audio frames are queued)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Action: status, stop, queue_size"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "verify_voiceprint",
        "description": (
            "Verify or enroll speaker identity from phone audio. "
            "Actions: "
            "'status' (check if voiceprint is enrolled), "
            "'enroll' (start enrollment — speak for 5+ seconds), "
            "'test' (test current enrollment against phone audio)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Action: status, enroll, test"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "smart_home",
        "description": (
            "Controls TP-Link Kasa smart home devices. "
            "Actions: 'power_on', 'power_off', 'toggle', 'set_brightness', "
            "'set_color', 'status', 'energy', 'info', 'discover'. "
            "Use 'discover' to auto-find all devices on the network. "
            "Devices are configured in api_keys.json under 'kasa_devices'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device": {
                    "type": "STRING",
                    "description": "Device name or IP (not needed for 'discover')"
                },
                "action": {
                    "type": "STRING",
                    "description": "Action: power_on, power_off, toggle, set_brightness, set_color, status, energy, info, discover"
                },
                "value": {
                    "type": "STRING",
                    "description": "Value for set_brightness (1-100) or set_color (name or H,S,V)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "manage_routines",
        "description": (
            "Create, list, enable, disable, or delete automation routines. "
            "Types: 'cron' (time-based), 'interval' (repeat), 'once' (one-shot). "
            "Examples: 'turn on lights at 7pm every weekday', "
            "'remind me in 30 minutes', 'check weather every 2 hours'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Action: create, list, enable, disable, delete"
                },
                "name": {
                    "type": "STRING",
                    "description": "Routine name (for create)"
                },
                "command": {
                    "type": "STRING",
                    "description": "Command to execute (for create)"
                },
                "routine_type": {
                    "type": "STRING",
                    "description": "Type: cron, interval, once (for create, default: cron)"
                },
                "schedule": {
                    "type": "OBJECT",
                    "description": "Cron schedule: {hour, minute, day_of_week} (for cron type)"
                },
                "interval": {
                    "type": "OBJECT",
                    "description": "Interval: {minutes, hours} (for interval type)"
                },
                "routine_id": {
                    "type": "STRING",
                    "description": "Routine ID or name (for enable/disable/delete)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "analyze_image",
        "description": (
            "Analyze an image file. Describes contents, detects objects, "
            "reads text (OCR), or answers questions about the image. "
            "Use for screenshots, photos, documents, etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "image_path": {
                    "type": "STRING",
                    "description": "Path to the image file"
                },
                "prompt": {
                    "type": "STRING",
                    "description": "Question about the image (default: describe it)"
                }
            },
            "required": ["image_path"]
        }
    },
    {
        "name": "manage_calendar",
        "description": (
            "Manage calendar events. Actions: "
            "'upcoming' (events in next N hours), 'add' (create event), "
            "'remove' (delete event), 'list' (all events)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Action: upcoming, add, remove, list"
                },
                "title": {
                    "type": "STRING",
                    "description": "Event title (for add)"
                },
                "time": {
                    "type": "STRING",
                    "description": "Event time in ISO format (for add)"
                },
                "description": {
                    "type": "STRING",
                    "description": "Event description (for add)"
                },
                "hours": {
                    "type": "INTEGER",
                    "description": "Hours ahead to check (for upcoming, default: 24)"
                },
                "event_id": {
                    "type": "STRING",
                    "description": "Event ID or title (for remove)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "set_location",
        "description": (
            "Set or get the user's location for context-aware responses. "
            "Provides timezone, local time, and geographic context."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {
                    "type": "STRING",
                    "description": "City name (empty to get current location)"
                },
                "region": {
                    "type": "STRING",
                    "description": "Region/state"
                },
                "country": {
                    "type": "STRING",
                    "description": "Country"
                }
            },
            "required": []
        }
    },
    {
        "name": "summarize_conversation",
        "description": (
            "Summarize the current or recent conversation. "
            "Useful for long conversations to extract key points."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "conv_id": {
                    "type": "INTEGER",
                    "description": "Conversation ID to summarize (current if omitted)"
                }
            },
            "required": []
        }
    },
    {
        "name": "learn_preference",
        "description": (
            "Explicitly learn a user preference. "
            "Use when the user states a preference, habit, or instruction. "
            "Categories: likes, dislikes, favorite, habit, instruction."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "Category: likes, dislikes, favorite, habit, instruction"
                },
                "key": {
                    "type": "STRING",
                    "description": "Preference key/name"
                },
                "value": {
                    "type": "STRING",
                    "description": "Preference value"
                }
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Show system performance metrics, memory usage, cache stats, "
            "and resource utilization. Use to diagnose performance issues."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
]


# ---------------------------------------------------------------------------
# Convert Gemini-style declarations to OpenAI/Ollama format
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "OBJECT": "object", "STRING": "string", "ARRAY": "array",
    "INTEGER": "integer", "BOOLEAN": "boolean", "NUMBER": "number",
}


def _convert_type(t: str) -> str:
    return _TYPE_MAP.get(t, t.lower()) if isinstance(t, str) else t


def _convert_props(props: dict) -> dict:
    out = {}
    for k, v in props.items():
        nv = dict(v)
        if "type" in nv:
            nv["type"] = _convert_type(nv["type"])
        if "items" in nv and isinstance(nv["items"], dict):
            nv["items"] = {"type": _convert_type(nv["items"].get("type", "string"))}
        out[k] = nv
    return out


def _to_ollama_tools(decls: list) -> list:
    tools = []
    for d in decls:
        params = d.get("parameters", {})
        new_params: dict = {
            "type":       "object",
            "properties": _convert_props(params.get("properties", {})),
        }
        req = params.get("required")
        if req:
            new_params["required"] = req
        tools.append({
            "type": "function",
            "function": {
                "name":        d["name"],
                "description": d["description"],
                "parameters":  new_params,
            },
        })
    return tools


OLLAMA_TOOLS = _to_ollama_tools(TOOL_DECLARATIONS)

# Core tools — always sent to the LLM (15 essential tools)
CORE_TOOL_NAMES = [
    "open_app", "web_search", "weather_report", "send_message", "reminder",
    "youtube_video", "computer_settings", "file_controller", "code_helper",
    "computer_control", "save_memory", "notes", "calculator", "timer",
    "shutdown_jarvis",
]
CORE_TOOLS = _to_ollama_tools([d for d in TOOL_DECLARATIONS if d["name"] in CORE_TOOL_NAMES])

# Flat list of canonical tool names — the authority other modules validate against.
TOOL_NAMES = [d["name"] for d in TOOL_DECLARATIONS]
