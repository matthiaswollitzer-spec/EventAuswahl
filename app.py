import io
import json
from google import genai
from google.genai import types
from PIL import Image
import streamlit as st

# Streamlit Seiteneinstellungen
st.set_page_config(
    page_title="WhatsApp Event Planner", page_icon="🎉", layout="wide"
)

st.title("🎉 Event Planner: Flyer-Analyse & Abstimmung")
st.write(
    "Lade Flyer oder Screenshots hoch. Die KI extrahiert die Event-Daten und erstellt automatisch eine Abstimmung."
)

# API Key Eingabe in der Sidebar
#api_key = st.sidebar.text_input("Google Gemini API Key", type="password")
# Versucht den Key automatisch aus secrets.toml zu laden, falls vorhanden
default_key = st.secrets.get("GEMINI_API_KEY", "")

# # API Key Eingabe in der Sidebar (vorausgefüllt, falls in secrets.toml vorhanden)
# api_key = st.sidebar.text_input(
#     "Google Gemini API Key", value=default_key, type="password"
# )
# st.sidebar.markdown(
#     "[Hier API Key kostenlos erstellen](https://aistudio.google.com)"
# )
# API Key unsichtbar im Hintergrund aus secrets.toml laden
try:
    api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    api_key = None
    st.error("Fehler: Kein 'GEMINI_API_KEY' in .streamlit/secrets.toml gefunden!")

# Session State für gespeicherte Events
if "events" not in st.session_state:
    st.session_state.events = []


def analyze_flyer(image, key):
    """Analysiert das Bild mit der Gemini API und gibt strukturierte JSON-Daten zurück."""
    client = genai.Client(api_key=key)
    prompt = """
    Analysiere diesen Flyer/Screenshot für ein Event oder eine Party. 
    Extrahiere folgende Informationen im exakten JSON-Format:
    {
        "title": "Name des Events",
        "date": "Datum oder Wochentag",
        "time": "Uhrzeit/Beginn",
        "location": "Ort/Club/Adresse",
        "description": "Kurze Zusammenfassung in 1 Satz"
    }
    """
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[image, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        ),
    )
    return json.loads(response.text)


# Bereich 1: Uploader
st.header("1. Flyer oder Screenshot hochladen")
uploaded_file = st.file_uploader(
    "Bild auswählen (PNG, JPG)", type=["png", "jpg", "jpeg"]
)

if uploaded_file and api_key:
    image = Image.open(uploaded_file)
    st.image(image, caption="Vorschau Upload", width=250)

    if st.button("Flyer analysieren & zur Abstimmung hinzufügen"):
        with st.spinner("Analysiere Bild mit Gemini API..."):
            try:
                event_data = analyze_flyer(image, api_key)
                event_data["votes"] = 0
                event_data["voters"] = []

                # Bild als Bytes für die Anzeige speichern
                buf = io.BytesIO()
                image.save(buf, format="PNG")
                event_data["image_bytes"] = buf.getvalue()

                st.session_state.events.append(event_data)
                st.success(
                    f"Event '{event_data.get('title')}' erfolgreich hinzugefügt!"
                )
            except Exception as e:
                st.error(f"Fehler bei der Analyse: {e}")
elif uploaded_file and not api_key:
    st.warning("Bitte gib deinen Gemini API Key in der linken Seitenleiste ein.")
    
st.divider()
st.header("2. Event-Übersicht & Abstimmung")

if not st.session_state.events:
    st.info("Noch keine Events vorhanden. Lade oben einen Flyer hoch!")
else:
    user_name = st.text_input(
        "Dein Name für die Abstimmung:", value="Max Mustermann"
    )

    for idx, event in enumerate(st.session_state.events):
        col1, col2 = st.columns([1, 2])

        with col1:
            if "image_bytes" in event:
                st.image(event["image_bytes"], width=250)

        with col2:
            st.subheader(event.get("title", "Unbekanntes Event"))
            st.write(
                f"📅 **Datum:** {event.get('date', 'N/A')} | ⏰ **Uhrzeit:** {event.get('time', 'N/A')}"
            )
            st.write(f"📍 **Ort:** {event.get('location', 'N/A')}")
            st.write(f"📝 {event.get('description', '')}")

            # Abstimmungs-Button
            if st.button(
                f"👍 Dafür stimmen ({event['votes']} Stimmen)", key=f"vote_{idx}"
            ):
                if user_name not in event["voters"]:
                    event["votes"] += 1
                    event["voters"].append(user_name)
                    st.rerun()
                else:
                    st.warning("Du hast für dieses Event bereits abgestimmt!")

            if event["voters"]:
                st.caption(f"Zustimmungen von: {', '.join(event['voters'])}")

        st.divider()

# Bereich 3: WhatsApp-Text-Export
if st.session_state.events:
    st.header("3. Zusammenfassung für WhatsApp")
    if st.button("WhatsApp-Text generieren"):
        summary = "🎉 *Wochenend-Ausflug Abstimmung* 🎉\n\n"
        for event in st.session_state.events:
            summary += f"🔹 *{event.get('title')}*\n"
            summary += (
                f"📅 {event.get('date')} um {event.get('time')}\n"
            )
            summary += f"📍 {event.get('location')}\n"
            voter_list = (
                ", ".join(event["voters"]) if event["voters"] else "Keine"
            )
            summary += (
                f"👍 Stimmen ({event['votes']}): {voter_list}\n\n"
            )

        st.text_area(
            "Kopiere diesen Text direkt in deine WhatsApp-Gruppe:",
            value=summary,
            height=200,
        )