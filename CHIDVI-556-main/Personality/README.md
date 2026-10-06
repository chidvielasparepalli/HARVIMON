# CHIDVI-556 Personality System

Persona files live directly in this folder. Each standalone persona is one Python
file exporting a `PROFILE` dictionary containing interaction/behavior, voice,
theme, greeting, and optional avatar settings.

## Add a persona

1. Create a file such as `Personality/my_persona.py`.
2. Export a `PROFILE` dictionary. Use `deadpool.py` as a complete example.
3. Restart the app (or call `discover_personalities()` again). The manager scans
   this directory automatically; no central registry edit is required.

The shared UI should read `manager.theme_config()` and apply it through its
existing theme renderer. Persona files configure the theme; they do not duplicate
UI widgets or layouts.

## Quick start

```python
from Personality import PersonalityManager

manager = PersonalityManager(default="deadpool", intensity=0.95)
prompt = manager.system_prompt(base_prompt="You are CHIDVI, a helpful AI assistant.")
theme = manager.theme_config()
voice = manager.voice_config()
greeting = manager.greeting()
manager.set_personality("hinata")
```

## Compatibility and integration

Existing built-in personas remain available through the legacy profile registry
until they are migrated to individual files. Standalone modules override a legacy
entry with the same ID.

This package discovers profiles and exposes prompt, voice, greeting, and theme
configuration. The host application's UI, model-request, persistence, and audio
playback wiring still needs to consume these values. Changing a profile does not
yet guarantee that the running PyQt UI visibly changes, or that chat history and
settings are persisted.
