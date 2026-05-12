import fnmatch
import tomllib
from pathlib import Path

import httpx
from platformdirs import user_config_dir
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool
from prefab_ui.app import PrefabApp
from prefab_ui.themes import Minimal
from prefab_ui.components import (
    Button, Column, Form, Heading, Input, Muted, Row, Textarea, Card, CardFooter, CardContent, H3
)
from prefab_ui.components.control_flow import Elif, ForEach, If
from prefab_ui.rx import ERROR, Rx, RESULT
from fastmcp import FastMCP, FastMCPApp

app = FastMCPApp("AnkiShell")


DEFAULT_CONFIG_TOML = """\
[anki]
endpoint = "http://localhost:8765"

[card]
default_type = "Cloze"

[decks]
filter = ["*"]
"""


def load_config() -> dict:
    config_path = Path(user_config_dir("ankishell")) / "config.toml"
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(DEFAULT_CONFIG_TOML)
    with open(config_path, "rb") as f:
        return tomllib.load(f)


config = load_config()
ANKI_URL = config.get("anki", {}).get("endpoint", "http://localhost:8765")
NOTE_TYPE = config.get("card", {}).get("default_type", "Cloze")
DECK_FILTER: list[str] = config.get("decks", {}).get("filter", [])


def anki(action: str, **params):
    payload = {"action": action, "version": 6, "params": params}
    r = httpx.post(ANKI_URL, json=payload)
    result = r.json()
    if result["error"]:
        raise ValueError(result["error"], payload)
    return result["result"]


@app.tool()
def add_note(deck: str, text: str) -> str:
    """Add a new note to Anki and return its ID."""
    note_id = anki(
        "addNote",
        note={
            "deckName": deck,
            "modelName": NOTE_TYPE,
            "fields": {"Text": text},
            "options": {"allowDuplicate": False, "duplicateScope": "deck"},
        },
    )
    return note_id


@app.ui(
    name="add_notes",
    description="""Propose changes to multiple Anki flashcards at once.
    You should always first call the `good_card_guidelines` tool to learn how to generate the list of notes.

    The parameters to send to this command must be:
    [
    {"deck": "DeckName", "text": "Card content with {{c1::cloze deletions}} and {{c2::additional fields}}"}},
    ...
    ]
    The user will be presented with an interactive dialog to confirm each card before it is created. Each card will be submitted individually, so if you send 5 notes, the user will see 5 confirmation dialogs.
    Do not create more than 5 notes.
    """,
)
def add_notes(notes: list[dict[str, str]] = [{}]) -> PrefabApp:
    notes_with_state = [{"submitted": False, "errored": False, "note_id": None, "error_message": None, **note} for note in notes]

    with Column(gap=4) as view:
        H3("Add Notes")

        with ForEach("notes") as (i, note):
            with Form(
                on_submit=CallTool(
                    "add_note",
                    arguments={"deck": note.deck, "text": note.text},
                    on_success=[
                        SetState(f"notes.{i}.submitted", True),
                        SetState(f"notes.{i}.errored", False),
                        SetState(f"notes.{i}.note_id", RESULT),
                        # ShowToast("Card created!", variant="success"),
                    ],
                    on_error=[
                        SetState(f"notes.{i}.errored", True),
                        SetState(f"notes.{i}.submitted", False),
                        SetState(f"notes.{i}.error_message", ERROR),
                    ],
                )
            ):
                with Card():
                    with CardContent():
                        Textarea(name=f"notes.{i}.text", value=note.text, required=True, rows=3)
                    with CardFooter():
                        with Column(gap=2):
                            with Row(gap=2):
                                Input(name=f"notes.{i}.deck", placeholder="Deck", value=note.deck, required=True)
                                Button(
                                    str(note.errored.then("Failed!", note.submitted.then("Added!", "Add"))),
                                    variant=note.errored.then("destructive", note.submitted.then("success", "default")),
                                    disabled=note.submitted,
                                )
                            with If(note.submitted):
                                with Row(gap=2, align="center"):
                                    Muted(f"Created note with nid {note.note_id}")
                                    Button(
                                        "Open in Anki",
                                        button_type="button",
                                        on_click=CallTool("open_in_anki", arguments={"note_id": note.note_id}),
                                        variant="outline",
                                        size="sm",
                                    )
                            with Elif(note.errored):
                                Muted(str(note.error_message))

    return PrefabApp(view=view, state={"notes": notes_with_state}, theme=Minimal(accent="blue"))


@app.tool()
def open_in_anki(note_id: int) -> str:
    """Open a note in the Anki card browser."""
    anki("guiBrowse", query=f"nid:{note_id}")
    return f"Opened note {note_id} in Anki"


mcp = FastMCP("AnkiShell", providers=[app])

@mcp.tool(
    name="list_decks",
    description="List all Anki decks available to the user."
)
def list_decks() -> list:
    """List all Anki decks."""
    all_decks = anki("deckNames")
    if not DECK_FILTER:
        return all_decks
    return [d for d in all_decks if any(fnmatch.fnmatch(d, pat) for pat in DECK_FILTER)]


@mcp.tool()
def search(query: str) -> list:
    """Search for Anki cards."""
    raise NotImplementedError("Not yet implemented")


@mcp.tool()
def unsuspend(card_ids: list[int]) -> str:
    """Unsuspend Anki cards by ID."""
    raise NotImplementedError("Not yet implemented")


@mcp.tool()
def get_config() -> dict:
    """Return the path to the AnkiShell config file and its current contents. If the user is interested in changing AnkiShell's configuration, they can edit this file directly. Help them make the changes needed if they request it by suggesting a new config file."""
    config_path = Path(user_config_dir("ankishell")) / "config.toml"
    return {
        "config_path": str(config_path),
        "config": open(config_path, "r").read() if config_path.exists() else "No config file found.",
    }


@mcp.tool()
def good_card_guidelines() -> str:
    """Instructions for creating Anki notes. Should always be called before `add_notes` to ensure the agent knows how to format the notes correctly."""
    return GOOD_CARD_GUIDE


@mcp.prompt()
def create_notes() -> str:
    """Instructions for creating Anki notes using the add_notes UI."""
    return GOOD_CARD_GUIDE
    
GOOD_CARD_GUIDE = """To create Anki notes, follow these steps:

    # STEPS TO CREATING NOTES
    1. **List available decks** — call the `list_decks` tool to get the names of decks the user has access to. Confirm the target deck with the user if it is unclear.

    2. **Prepare the notes** — build a list of note objects. Each note must have:
    - `deck`: the exact deck name returned by `list_decks`
    - `text`: the card content as a string

    The cards must be in cloze format. Wrap the answer portion with `{{c1::...}}` syntax, e.g. `The capital of France is {{c1::Paris}}`.

    3. **Call the `add_notes` UI** — pass the list as the `notes` argument:
    ```json
    [
        {"deck": "MyDeck", "text": "The capital of France is {{c1::Paris}}"},
        {"deck": "MyDeck", "text": "Water boils at {{c1::100°C}}"}
    ]
    ```
   Each note will be submitted individually via the UI form.

    At MOST, you should create 5 notes at a time to avoid overwhelming the user with too many form submissions.
    If there are more than 5 notes to create, split them into 'topics' and ask the user which topic they prefer.

    # GUIDELINES FOR MAKING GOOD CARDS
    When creating Anki notes, follow these guidelines to ensure they are effective for learning:
    Cloze deletions are the only acceptable form of card to use. They have the format {{{{c1::text::placeholder}}}}
    Keep the text concise and focused on a single concept. Sentences should ideally be no longer than 20 words.
    A good example of a card is:

    One clinical feature of {{c1::Addison disease}} is {{c2::hyperpigmentation}}, due to {{c3::↑ melanocyte-stimulating hormone 2/ ↑ ACTH}}.

    Related knowledge can be grouped together, such as:
    In T2DM, insulin is typically initiated with {{c1::basal insulin}} (e.g. {{c1::glargine/detemir}}) at {{c2::10 units}} ({{c2::0.1–0.2 units }}/kg) at bedtime.
    Examples of things which are 'related knowledge' are:
    - Drug class and name (e.g. "basal insulin" and "glargine/detemir")
    - Dosing calculation by different methods (e.g. "10 units" and "0.1–0.2 units/kg")

    Things which are NOT 'related knowledge' include:
    - A disease and its clinical feature (e.g. "Addison disease" and "hyperpigmentation")
    - A clinical feature and its pathophysiology (e.g. "hyperpigmentation" and "↑ melanocyte-stimulating hormone 2/ ↑ ACTH")
    - Drug timing and dosing (e.g. "basal insulin" and "once daily")
    """

def main():
    mcp.run()
