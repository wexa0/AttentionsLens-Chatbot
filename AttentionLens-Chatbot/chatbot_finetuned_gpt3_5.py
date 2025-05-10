import os
import re
import json
import threading
import dateparser
import firebase_admin
from firebase_admin import credentials, firestore
from bs4 import BeautifulSoup
from openai import OpenAI
import psutil
import time
from datetime import datetime, timedelta
import unicodedata
from dateparser.search import search_dates
import pytz
from spellchecker import SpellChecker



print(f"🧠 CPU Info: {psutil.cpu_count(logical=False)} physical cores, {psutil.cpu_count(logical=True)} logical cores")

if not firebase_admin._apps:        
    cred = credentials.Certificate("attensionlens-db-firebase-adminsdk-fsdxz-8d6dc28e0f.json")
    firebase_admin.initialize_app(cred)


db = firestore.client()


client = OpenAI(api_key="") #Add Our Key Here
model_id = "ft:gpt-3.5-turbo-0125:ksu::BHDqlIOS"


def sanitize_text(text):
    normalized = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in normalized if is_valid_utf(c))

def correct_message_input(user_message):
    spell = SpellChecker()
    corrected = [spell.correction(word) or word for word in user_message.split()]
    return " ".join(corrected)



def user_requested_relative_time(user_message):
    """Detect if user wants reminder relative to task time."""
    keywords = ["before", "earlier", "ahead of"]
    user_message = user_message.lower()
    return any(word in user_message for word in keywords)


def parse_relative_amount_unit(message):
    import re
    match = re.search(r'(\d+)\s*(minutes?|hours?|days?)\s*(before|earlier)?', message.lower())
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        return amount, unit
    return None, None

def user_provided_explicit_time(text: str) -> bool:
    # Looks for explicit times like "5 PM", "17:00", or "6:30 am"
    return bool(re.search(r'\b\d{1,2}(:\d{2})?\s*(am|pm)?\b', text, re.IGNORECASE))

def extract_task_title_and_time(message):
    now = datetime.now(pytz.timezone('Asia/Riyadh'))
    original_message = message
    message = message.lower()

    # Step 1: Check for relative time
    is_relative = False
    relative_match = re.search(r'(\d+)\s*(minutes?|hours?|days?)\s*(before|earlier)?', message)
    if relative_match:
        is_relative = True
        message = message.replace(relative_match.group(0), '')

    # Step 2: Extract datetime (absolute)
    reminder_datetime = None
    results = search_dates(message, settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now})
    if results:
        reminder_datetime = results[-1][1]

    # Step 3: Determine if user gave a specific time
    has_time = user_provided_explicit_time(original_message)

    # Step 4: Extract task title
    quoted_title = re.findall(r'"(.*?)"', original_message)
    if quoted_title:
        task_title = quoted_title[0].strip()
    else:
        match = re.search(r"(?:for|about)\s+(.*?)(?:\s+(to\sremind|to|before|after|by|at|on|in)|$)", message)
        if match:
            task_title = match.group(1).strip()
        else:
            task_title = message.strip()

    # Step 5: Clean title
    garbage_phrases = [
        "i want to set", "i want to", "set a reminder", "remind me", "to remind",
        "before", "after", "at", "on", "in", "by", "to", "me", "it"
    ]
    pattern = r'\b(?:' + '|'.join(re.escape(word) for word in garbage_phrases) + r')\b'
    task_title = re.sub(pattern, '', task_title, flags=re.IGNORECASE)
    task_title = re.sub(r'\s+', ' ', task_title).strip()

    return task_title, reminder_datetime, is_relative, has_time



def extract_dates_from_message(message: str):
    message = message.lower()
    extracted = []

    now = datetime.now()

    if not any(word in message for word in [
        "today", "tomorrow", "yesterday", "this week", "this month", "after tomorrow",
        "saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday",
        "january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december", "2024", "2025"
    ]):
        return []

    results = search_dates(
        message,
        settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now,
            "STRICT_PARSING": True,  
            "RETURN_AS_TIMEZONE_AWARE": False
        }
    )

    if results:
        for _, dt in results:
            date_str = dt.strftime('%Y-%m-%d')
            if date_str not in extracted:
                extracted.append(date_str)

    if "today" in message:
        extracted.append(now.strftime('%Y-%m-%d'))

    if "yesterday" in message:
        extracted.append((now - timedelta(days=1)).strftime('%Y-%m-%d'))

    if "tomorrow" in message:
        extracted.append((now + timedelta(days=1)).strftime('%Y-%m-%d'))

    if "after tomorrow" in message:
        extracted.append((now + timedelta(days=2)).strftime('%Y-%m-%d'))

    if "this week" in message:
        start = now - timedelta(days=now.weekday())
        for i in range(7):
            extracted.append((start + timedelta(days=i)).strftime('%Y-%m-%d'))

    if "this month" in message:
        start = now.replace(day=1)
        next_month = start.replace(day=28) + timedelta(days=4)
        last_day = next_month - timedelta(days=next_month.day)
        for i in range(last_day.day):
            extracted.append((start + timedelta(days=i)).strftime('%Y-%m-%d'))

    if "weekend" in message:
        saturday = now + timedelta((5 - now.weekday()) % 7)
        sunday = saturday + timedelta(days=1)
        extracted.extend([saturday.strftime('%Y-%m-%d'), sunday.strftime('%Y-%m-%d')])


    return list(set(extracted))



def get_category_name(task_id):
    category_ref = db.collection("Category").where("taskIDs", "array_contains", task_id)
    category_docs = category_ref.stream()
    for doc in category_docs:
        return doc.to_dict().get("categoryName", "None")
    return "None"


def add_task_handler(user_id, user_message, doc_ref):
    try:
        if user_id.startswith("guest_"):
            doc_ref.update({
                "response": "🌟 Hey there! It looks like you're using a guest account. To create and save your tasks, please register! We'd love to have you as a part of our community! 😊",
                "actionSuggestion": "registerNow"
            })
            print("📌 User is a guest, suggested registration.")
            return

        print("📌 Trying to extract task info from user input...")
        today_str = datetime.now().strftime("%Y-%m-%d")

        extract_info_prompt = f"""
        You are a JSON extraction tool. 🧰

        Extract ONLY the following fields from the user's input and return them as valid JSON:
        - title: the task title or name
        - date: the due date in yyyy-mm-dd format
        - time: the time in HH:mm (24-hour format)
        - subtasks: a list of subtasks (if available), with each subtask being a string, up to 10 subtasks
        - note: any additional notes related to the task (if available)

        ⚠️ If a field is not found, return null. DO NOT guess or include extra data.
        🗓️ Assume today's date is {today_str} when the user says "today", "tomorrow", etc.

        🎯 FORMAT:
        {{
            "title": "Buy milk", 
            "date": "2025-04-20", 
            "time": "15:30", 
            "subtasks": ["Subtask 1", "Subtask 2"],
            "note": "Remember to check for discounts on milk."
        }}

        User: "{user_message}"
        """

        info_response = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "system", "content": extract_info_prompt}],
            max_tokens=200,
            temperature=0.2,
        )

        raw_json = info_response.choices[0].message.content.strip()
        print("📦 Raw extracted JSON:", raw_json)

        try:
            task_info = json.loads(raw_json)
        except json.JSONDecodeError:
            print("⚠️ GPT response was not valid JSON. Prompting manual entry.")
            doc_ref.update({
                "response": "📝 I couldn't extract task details from that. Please fill them in below manually.",
                "actionSuggestion": "addition"
            })
            return

        if not any([
            task_info.get("title"), task_info.get("date"), task_info.get("time"),
            task_info.get("subtasks"), task_info.get("note")
        ]):
            print("⚠️ Extracted JSON was empty. Prompting manual entry.")
            doc_ref.update({
                "response": "📝 I couldn't find enough details from what you said. Want to fill them in yourself below?",
                "actionSuggestion": "addition"
            })
            return

        update_data = {
            "actionSuggestion": "addition",
            "response": "✅ Got it! You can review and edit your task details below.",
            "title": task_info.get("title") or "",
            "date": task_info.get("date") or "",
            "time": task_info.get("time") or "",
            "subtasks": task_info.get("subtasks") or [],
            "note": task_info.get("note") or "",
        }

        doc_ref.update(update_data)
        print("📤 Sent extracted task info to Flutter ✉️")

    except Exception as e:
        print(f"❌ Error in add task handler: {e}")
        doc_ref.update({
            "response": "😔 Sorry, I had trouble setting up your task. You can still add it manually below.",
            "actionSuggestion": "addition"
        })



def format_task(task, task_id):
    subtasks_ref = db.collection("SubTask").where("taskID", "==", task_id)
    subtask_docs = subtasks_ref.stream()

    subtasks_list = [doc.to_dict() for doc in subtask_docs]
    subtasks_text = "\n".join([
        f"      • {st.get('title')}  {'✅' if st.get('completionStatus') == 1 else '⬜️'}"
        for st in subtasks_list
    ]) or "      • No subtasks"


    scheduled_datetime = task.get('scheduledDate')
    if scheduled_datetime:
        try:
            if hasattr(scheduled_datetime, "to_datetime"):  # Firestore Timestamp
                scheduled_datetime = scheduled_datetime.to_datetime()
            elif isinstance(scheduled_datetime, str):
                scheduled_datetime = dateparser.parse(scheduled_datetime)

            # 🕒 Adjust timezone if needed
            tz = pytz.timezone("Asia/Riyadh")
            scheduled_datetime = scheduled_datetime.astimezone(tz)

            date_str = scheduled_datetime.strftime('%A, %d %B %Y')  # Example: Thursday, 08 May 2025
            time_str = scheduled_datetime.strftime('%I:%M %p')       # Example: 08:55 PM
        except:
            date_str = 'Invalid date'
            time_str = 'Invalid time'
    else:
        date_str = 'Not set'
        time_str = 'Not set'

    title = task.get('title', 'Untitled Task').strip()
    category_name = get_category_name(task_id)
    return f"""
📝 **Task Name:** {title}

📅 **Date:** {date_str}
🕒 **Time:** {time_str}
🔔 **Reminder:** {task.get('reminder', 'None')}
⚡ **Priority:** {task.get('priority', 'N/A')}
📂 **Category:** {category_name}
🗒️ **Note:** {task.get('note', '-') if task.get('note') else '-'}

📌 **Status:** {"✅ Completed" if task.get('completionStatus') == 2 else "⏳ Pending"}

🧩 **Subtasks:**
{subtasks_text}
"""


def find_tasks_by_dates(user_id, user_message):
    extracted_dates = extract_dates_from_message(user_message)
    if not extracted_dates:
        return None

    matched_tasks = []
    for date_text in extracted_dates:


        parsed_date = dateparser.parse(date_text, settings={"PREFER_DAY_OF_MONTH": "first", "RELATIVE_BASE": datetime.now()})

        if not parsed_date:
            continue

        start_of_day = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 0, 0, 0)
        end_of_day = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 23, 59, 59)

        query = db.collection("Task").where("userID", "==", user_id) \
            .where("scheduledDate", ">=", start_of_day) \
            .where("scheduledDate", "<=", end_of_day) \
            .order_by("scheduledDate")  

        for doc in query.stream():
            matched_tasks.append({"id": doc.id, **doc.to_dict()})


    return matched_tasks if matched_tasks else None



def handle_view_schedule(user_id, user_message, doc_ref, task_name=None):

    if user_id.startswith("guest_"):

        guest_msg = (
            "Sorry! The **View My Tasks** feature is only available for registered users. 🧠\n\n"
            "Create an account now to enjoy the full experience with smart task management, reminders, and progress tracking ✨\n\n"
            "📲 **Sign up now** to unlock all features!"
        )
        doc_ref.update({
            "response": guest_msg,
            "actionSuggestion": "registerNow"
        })
        print("🛑 Guest user tried to access View Schedule.")
        return

    try:
        extracted_dates = extract_dates_from_message(user_message)
        if not extracted_dates and task_name:

            print("🔍 Trying to find by task title...")
            query = db.collection("Task").where("userID", "==", user_id).where("title", "==", task_name)
            tasks = [{"id": doc.id, **doc.to_dict()} for doc in query.stream()]

            if tasks:
                response = "✅ Here’s the task you asked for:\n\n" + "\n\n".join(
    [format_task(t, t["id"]) for t in tasks if "id" in t]
)
                doc_ref.update({"response": response})
                print("📌 Sent task by title")
                return

        tasks = find_tasks_by_dates(user_id, user_message)
        extracted_dates = extract_dates_from_message(user_message)

        if extracted_dates:
            extracted_dates.sort(key=lambda x: dateparser.parse(x)) 

            all_responses = []
            for date_text in extracted_dates:
                parsed_date = dateparser.parse(date_text)
                if not parsed_date:
                    continue

                start_of_day = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 0, 0, 0)
                end_of_day = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 23, 59, 59)

                query = db.collection("Task").where("userID", "==", user_id) \
                                            .where("scheduledDate", ">=", start_of_day) \
                                            .where("scheduledDate", "<=", end_of_day)

                tasks_for_day = [{"id": doc.id, **doc.to_dict()} for doc in query.stream()]
                date_label = parsed_date.strftime('%Y-%m-%d')

                print(f"📋 Tasks found for {date_label}: {[t['title'] for t in tasks_for_day]}")


                message_for_user = ""

                if not tasks_for_day:
                    message_for_user = (
                                        "😔 Sorry, I couldn’t find any tasks to display for that day.\n\n"
                                        "💡 Let’s boost your productivity! You can add a new task by typing **Add a task** ✨\n"
                                        "Or choose your preferred date from the calendar 📅"
                                    )

                elif date_text == datetime.now().strftime("%Y-%m-%d"):
                    message_for_user = (
                        "🔥 It's today! Let's get things done step by step 💪\n"
                        "Remember: Start small, stay consistent ✨"
                    )
                elif len(tasks_for_day) == 1:
                    message_for_user = (
                        "🧐 Only one task for this day? That's totally fine!\n"
                        "But maybe adding another tiny goal could boost your momentum 🚀"
                    )
                elif len(tasks_for_day) <= 3:
                    message_for_user = (
                        "✨ Nice and light schedule!\n"
                        "Don't forget to celebrate every small win 🏆"
                    )
                elif "this month" in user_message.lower() or len(extracted_dates) >= 15:
                    message_for_user = (
                        "📅 Wow! Looking at your whole month?\n"
                        "That's a great way to plan ahead and avoid surprises 🔥"
                    )
                else:
                    message_for_user = (
                        "👏 You're doing amazing organizing your tasks!\n"
                        "Keep balancing between focus and rest 💡"
                    )


                # Add the message after tasks
                if tasks_for_day:
                    formatted_tasks = "\n\n".join([format_task(t, t["id"]).strip() for t in tasks_for_day])
                    all_responses.append(f"📅 **Tasks for {date_label} :**\n\n{formatted_tasks}\n\n{message_for_user}")
                else:
                    all_responses.append(f"📅 **Tasks for {date_label} :**\n\n😔 Sorry, I couldn’t find any tasks to display for that day.\n\n💡 Let’s boost your productivity! You can add a new task by typing **Add a task** ✨\nOr choose your preferred date from the calendar 📅")


            if all_responses:
                successful_responses = [r for r in all_responses if "📅 **Tasks for" in r]
                
                final_response = ""

                if successful_responses:
                    final_response += "🧠 Sure! Let me fetch your tasks \n\n" + "\n\n".join(successful_responses)

                    final_response = final_response.strip() + f"\n\n{message_for_user}"


                    doc_ref.update({"response": final_response})

                    print("✅ Sent grouped tasks for multiple dates.")


                else:
                    
                    fallback_msg = (
                        "😔 Sorry, I couldn’t find any tasks to display for that day.\n\n"
                        "💡 Let’s boost your productivity! You can add a new task by typing **Add a task** ✨\n\n"
                        "Or choose your preferred date from the calendar 📅"
                    )
                    doc_ref.update({
                        "response": fallback_msg,
                        "actionSuggestion": "openCalendar"
                    })
                    print("📭 No tasks found — fallback message with calendar suggestion.")


        
        elif extract_dates_from_message(user_message):
            response = "📭 No tasks found for the selected date(s)."
            doc_ref.update({"response": response})
            print("📭 No tasks found for selected date(s).")
        else:
    # No tasks or no dates found
            response = """
To view your tasks:

📅 **Select the date(s)** you'd like to check in the calendar 
Then I’ll show you the tasks with full details 😊
        """
            doc_ref.update({
                "response": response,
                "actionSuggestion": "openCalendar"
            })
            print("📅 Calendar fallback triggered.")

    except Exception as e:
            print(f"❌ Error in handle_view_schedule: {e}")
            doc_ref.update({"response": "😔 Sorry, I had trouble fetching your tasks. Please try again later."})


def is_valid_utf(text):
    try:
        text.encode('utf-16')
        return True
    except UnicodeEncodeError:
        return False
    

def on_snapshot(col_snapshot, changes, read_time):
    global last_processed_doc_id
    global last_handled_message 

    if not hasattr(on_snapshot, "handled_messages"):
        on_snapshot.handled_messages = {}

    if not hasattr(on_snapshot, "processed_docs"):
        on_snapshot.processed_docs = set()

    for change in changes:
        if change.type.name != "ADDED":
            continue

        data = change.document.to_dict()

        # Skip if already has a response
        if data.get("response"):
            continue

        # Mark as processed
        on_snapshot.processed_docs.add(change.document.id)

        # Extract fields
        user_id = data.get("userID")
        original_message = data.get("message")

        if not user_id or not original_message:
            print("❌ Missing userID or message.")
            continue

        # Correct the message input
        user_message = original_message  # ✅ هنا نحل المشكلة
        corrected_message = correct_message_input(original_message)
        doc_ref = change.document.reference

        # Track last processed
        last_processed_doc_id = change.document.id
        last_handled_message = user_message
        on_snapshot.handled_messages[user_id] = user_message

        print(f"📩 Message from {user_id}: {user_message}")

        # Check last 2 messages from user to continue flow if needed
        chat_ref = db.collection("ChatBot") \
                     .where("userID", "==", user_id) \
                     .order_by("timestamp", direction=firestore.Query.DESCENDING) \
                     .limit(2)
        last_messages = list(chat_ref.stream())

        for m in last_messages:
            entry = m.to_dict()
            if entry.get("response") and entry.get("actionSuggestion") == "awaiting_delete_info":
                print("🔁 Detected awaiting_delete_info from previous response.")
                handle_delete_task(user_id, user_message, doc_ref)
                return

            # Uncomment and complete this if you plan to support multiple reminder options
            # if entry.get("response") and entry.get("actionSuggestion") == "awaiting_reminder_choice":
            #     print("🔁 Detected awaiting_reminder_choice from previous response.")
            #     pending_options = entry.get("pendingReminderOptions", [])
            #     extracted_time = entry.get("extractedReminderTime")
            #     handle_reminder_choice(user_id, user_message, doc_ref, pending_options, extracted_time, last_messages)
            #     return


            system_instruction = """
            Sure! Let me guide you through some tips 😊

You are Attena, a friendly and encouraging ADHD assistant designed to motivate users, provide clear step-by-step suggestions, and use light, positive language. 
Always be supportive, use emojis occasionally to create a friendly vibe 😊, and suggest helpful follow-up questions. 

Always start your reply with a friendly phrase like “Sure! Let me help 😊 or Absolutely! Here's a quick tip 🔍


- **Purpose:** Specialized chatbot for individuals with ADHD, particularly focused on ADHD-I and ADHD-C.
- **Integration:** Fully integrated with the AttentionLens app, designed to enhance productivity, focus, organization, and help solve problems related to ADHD.

🚀 **Core Functions:**
1. **Attention Management and Productivity:**
   - Assist users in managing attention deficits and enhancing productivity. Provide strategies for improving concentration, time management, and daily routines. 
   - Facilitate task addition, modification, deletion, and reminder settings through the AttentionLens app.
   - Avoid discussing theological, religious, or political debates. Direct users seeking advice on such topics to seek expert guidance.

2. **Medical Advisory Limitation:**
   - Clarify that you are not a medical professional. Do not provide medical diagnoses or medication prescriptions. 
   - If asked medical-related questions, advise users to consult qualified healthcare providers.

3. **Cultural Sensitivity and Inclusion:**
   - Respect diverse backgrounds. Provide inclusive responses and facilitate culturally relevant discussions about ADHD and productivity without engaging in political or religious debates.

4. **Information Reliability:**
   - Ensure all responses are based on well-established knowledge and best practices from credible sources like research articles and expert-reviewed materials. Avoid citing specific sources directly.

5. **User Interaction Enhancement:**
   - Recognize and adapt to various user expressions. Use positive reinforcement and provide structured, step-by-step guidance to effectively engage users.
   - Encourage ongoing interaction by suggesting follow-up questions, related discussions, and using engaging elements like emojis and formatting.

6. **Emotional Support:**
   - Offer emotional support and strategies to manage daily ADHD-related challenges. Encourage users through positive interaction and motivation.

7. **Query Interpretation Flexibility:**
   - Flexibly interpret user queries, recognizing different phrasings, spelling errors, and typos. Ensure responses remain accurate and relevant to ADHD, irrespective of text casing.

8. **Data Privacy and Security:**
   - Handle all user data responsibly. Maintain privacy and security at all times to create a personalized and secure environment for the user.

9. **Accuracy and Relevance:**
   - Focus on generating responses that are factual and relevant. Avoid creating random or unrelated responses. 

10. **Dynamic Task Management:**
   - Enable users to dynamically manage tasks within the AttentionLens app. This includes adding, deleting, modifying tasks, and setting reminders.

🔒11. **Privacy and Security:**
- Commit to protecting user privacy and ensuring data security. Use user data only to enhance personalization and functionality within the chatbot environment.

12. 🚫 **Strict Boundaries – Topic Restrictions:**

You are strictly limited to discussing topics related to:
- ADHD (especially ADHD-I and ADHD-C)
- Focus, attention, time management, organization, and task productivity
- Task management inside the AttentionLens system (adding, deleting, updating tasks, reminders, etc.)

❌ Do NOT answer any question that is:
- Unrelated to ADHD or task management
- General knowledge (e.g., countries, science, math, history, religion, politics, trivia, etc.)
- Outside the scope of your defined role

✅ If the user asks about anything outside your domain, respond clearly and politely:

**"I’m here to help only with ADHD-related topics and productivity support 😊. For other types of questions, it’s best to consult a relevant expert!"**

"""

            style_instruction = """
You are Attena, a warm and encouraging virtual assistant that helps individuals with ADHD stay focused, motivated, and productive.

📌 Always organize your response into:
- A short intro phrase (friendly and positive)
- 2 to 4 bullet points (with emojis when possible)
- A follow-up motivational line or question

✔️ Use simple, friendly language and structure.
✔️ Use emojis naturally (✨💡🧠📌✅⏳🚀) to keep the tone light and ADHD-friendly.
✔️ Provide tips or examples in your bullet points when needed.
✔️ Keep responses short, clear, and direct — avoid long paragraphs.
✔️ Avoid being robotic. Use warmth, encouragement, and empathy.
✔️ You are also flexible in understanding spelling mistakes, typos, and informal language — interpret user input intelligently even if it’s not perfectly written.
✔️ Show empathy and understanding, especially when users express frustration or difficulty.  

"""

            classification_instruction = """
You are a strict and precise classifier. Your job is to classify user requests into one of the following **functional categories** for an ADHD-focused assistant. 

🔒 ONLY return the class name. No explanation. No formatting. No extra text.

Here are the categories:

• Add a Task – When the user asks to create or add a new task, even if they use informal words like "I need to do something".

• Add a Reminder – When the user mentions wanting to be reminded about a task, event, or anything time-based.

• Edit Task – When the user wants to change details of an existing task.

• Delete Task – When the user wants to remove a task from their list.

• Breakdown Task – When the user asks to break a big task into smaller subtasks.

• View My Schedule – When the user asks to see their tasks, to-do list, calendar, upcoming tasks, past tasks, or specific task info.

• Task/ADHD-related Question – For all questions related to ADHD challenges (especially ADHD-I or ADHD-C), including focus, organization, time management, productivity tips, reminders, or struggles with tasks.

• General-Accepted – For friendly messages like:
    - Greetings: "Hi", "Hey there", "Good morning"
    - Assistant questions: "Who are you?", "What can you do?", "What’s your name?"
    
• General-Rejected – For questions that are **completely unrelated**, including:
    - Food, recipes
    - Countries, geography
    - Science, history, math
    - Politics, religion
    - Medical questions about diagnosis/medication
    - Anything outside ADHD, productivity, or task management

• Guest User – For questions where the user asks about guest mode, limitations of being a guest, or what guests can do.

❗ Examples of General-Accepted:
- “Shismak?” → General-Accepted ✅
- “Hello bot” → General-Accepted ✅
- “Can you help me?” → General-Accepted ✅

❌ DO NOT misclassify friendly or assistant-related messages as General-Rejected.

Now classify this request accurately:
{prompt}
"""

            
                    # Step 1: Classify the user request and store it as an actionType
            classification_prompt = classification_instruction.format(prompt=corrected_message)
            classification_response = client.chat.completions.create(
                    model=model_id,
                    messages=[{"role": "system", "content": classification_prompt}],
                    max_tokens=10,
                    temperature=0.0,
                    stream=False
                )
            action_type = classification_response.choices[0].message.content.strip()
            print(f"🟢 Classified action: {action_type}")
            doc_ref.update({"actionType": action_type})

            if action_type == "Add a Task" or action_type == "Add Task":
                        time.sleep(0.5)
                        try:
                            print(f"✅ Starting the task addition for: {user_id}")
                            # doc_ref.update({"taskStatus": "in_progress"})
                            add_task_handler(user_id, user_message, doc_ref)
                            print(f"✅ Task added for: {user_id}")
                        
                            ignore_reply_categories = [
                                "Add a Task", "Add Task", "View My Schedule", "Add a Reminder", "Edit Task", "Delete Task", "Breakdown Task"
                            ]
                            if action_type in ignore_reply_categories:
                                print(f"🛑 Skipping response for action: {action_type}")
                                continue  
                    
            
                        except Exception as e:
                            print(f"❌ Error while adding task: {e}")
                            doc_ref.update({"response": "Sorry 😔 something went wrong while adding the task."})
                            continue
                    

            if action_type == "View My Schedule":
                    try:
                        task_name_prompt = f"""
                The user wants to view a task. Extract the task name they are asking about and return it in plain text only.
                User Message: "{user_message}"
                Just return the task name only:
                """
                        name_response = client.chat.completions.create(
                            model=model_id,
                            messages=[{"role": "system", "content": task_name_prompt}],
                            max_tokens=30,
                            temperature=0.2,
                        )
                        task_name = name_response.choices[0].message.content.strip()
                        print(f"🔎 Extracted task name: {task_name}")

                        handle_view_schedule(user_id, user_message, doc_ref, task_name)


                        print(f"✅ Sent task details for: {task_name}")
                        return  
                    except Exception as e:
                        print(f"❌ Error while fetching task info: {e}")
                        doc_ref.update({"response": "Sorry 😔 something went wrong while looking for your task."})

                    except Exception as e:
                        print(f"❌ Error during classification: {e}")
                    try:
                        doc_ref.update({"actionType": "Unknown"})
                    except:
                            pass
                            continue

                    
                    ignore_reply_categories = [
                        "Add a Task", "Add a Reminder", "Edit Task", "Delete Task", "Breakdown Task"
                    ]
                    if action_type in ignore_reply_categories:
                        print(f"🛑 Skipping response for action: {action_type}")
                        continue  
                            
                    # 🗑 Delete Task
            elif action_type == "Delete Task":
                try:
                    handle_delete_task(user_id, user_message, doc_ref)
                    print(f"🗑️ Delete task preview sent.")
                except Exception as e:
                    print(f"❌ Error in handle_delete_task: {e}")
                    doc_ref.update({"response": "⚠️ Something went wrong while preparing to delete your task."})
                continue
            #Breakdown tasks
            elif action_type == "Breakdown Task":
                try:
                    print("🛠 Starting Breakdown Task flow... (initial)")

                    updated_snapshot = doc_ref.get()
                    updated_data = updated_snapshot.to_dict()

                    message = updated_data.get("message", "")
                    user_message = updated_data.get("userMessage")
                    show_breakdown_form = updated_data.get("show_breakdown_form", False)
                    response = updated_data.get("response", "")

                    print(f"🔎 [Updated] message: {message}")
                    print(f"🔎 [Updated] userMessage: {user_message}")
                    print(f"🔎 [Updated] show_breakdown_form: {show_breakdown_form}")
                    print(f"🔎 [Updated] response: {response}")

                    # ✅ ✅ Always check if the message is a direct breakdown request
                    if isinstance(message, str) and message.lower().startswith("please break down the task"):
                        print("⚡ Detected direct breakdown request from message!")

                        # 👉 Extract task_name and estimated_time from the message
                        import re

                        match = re.search(r"task '(.*?)' which will take about (.*?)[\.\n]?$", message)
                        if match:
                            task_name = match.group(1)
                            estimated_time = match.group(2)

                            print(f"📋 Extracted task_name: {task_name}")
                            print(f"⏳ Extracted estimated_time: {estimated_time}")

                            steps = create_steps(task_name)
                            breakdown_response = format_breakdown_response(steps, estimated_time)

                            print(f"📜 Final breakdown response ready: {breakdown_response}")

                            # 🔥 Update Firestore with breakdown
                            doc_ref.update({
                                "response": breakdown_response,
                                "show_breakdown_form": False,
                            })
                            print("✅ Breakdown response successfully sent to Firestore.")
                        else:
                            print("⚠️ Could not extract task details from message.")
                            doc_ref.update({
                                "response": "⚠️ Sorry, I couldn't understand the task you want to break down.",
                                "show_breakdown_form": False,
                            })

                        continue  # 💥 Done after processing

                    # ✅ If no direct message, fall back to form handling
                    if not isinstance(user_message, dict) or not user_message.get("task_name") or not user_message.get("estimated_time"):
                        if show_breakdown_form:
                            print("📝 Form already shown. Waiting for user submission...")
                            continue
                        elif response == "pending_submission":
                            print("⌛ Waiting for backend to process submitted form...")
                            continue
                        else:
                            print("✍️ No valid userMessage yet. Showing the form now...")
                            doc_ref.update({
                                "show_breakdown_form": True,
                                "response": "✍️ Please fill out the task details below!",
                            })
                            continue

                    # ✅ User submitted the form manually (rare case)
                    print("✅ Received user form submission (legacy form flow).")
                    print(f"📋 task_name: {user_message.get('task_name')}")
                    print(f"📋 estimated_time: {user_message.get('estimated_time')}")

                    task_name = user_message["task_name"]
                    estimated_time = user_message["estimated_time"]

                    steps = create_steps(task_name)
                    breakdown_response = format_breakdown_response(steps, estimated_time)

                    print(f"📜 Final breakdown response ready: {breakdown_response}")

                    doc_ref.update({
                        "response": breakdown_response,
                        "show_breakdown_form": False,
                    })
                    print("✅ Breakdown response successfully sent to Firestore.")

                except Exception as e:
                    print(f"❌ Error in handle_breakdown_task: {e}")
                    doc_ref.update({
                        "response": "⚠️ Something went wrong while preparing to break down your task."
                    })
                continue

            elif action_type == "Add a Reminder":
                handle_add_reminder(user_id, user_message, doc_ref)
                return
                    
            if action_type == "General-Rejected":
                doc_ref.update({
                    "response": "I'm here to help only with ADHD-related topics and productivity 😊. For anything else, it's best to consult a relevant source!"
                })
                print("🚫 Rejected general question.")

                continue

            # After checking last_messages
            for m in last_messages:
                entry = m.to_dict()
                if entry.get("response") and entry.get("actionSuggestion") == "awaiting_reminder_time":
                    task_title = entry.get("pendingTaskTitle")  # ✅ Get stored task title
                    reminder_datetime = dateparser.parse(user_message)  # ✅ Only parse time from user_message
                    if reminder_datetime and reminder_datetime.tzinfo:
                        reminder_datetime = reminder_datetime.replace(tzinfo=None)

                    if reminder_datetime:
                        # Fetch task by title
                        task_docs = db.collection('Task').where('userID', '==', user_id).where('title', '==', task_title).stream()
                        tasks = [{"id": doc.id, **doc.to_dict()} for doc in task_docs]

                        if len(tasks) == 1:
                            task = tasks[0]
                            task_ref = db.collection('Task').document(task['id'])

                            if user_requested_relative_time(user_message):
                                scheduled_datetime = task.get('scheduledDate')

                                amount, unit = parse_relative_amount_unit(user_message)

                                if isinstance(scheduled_datetime, datetime) and amount:
                                    if scheduled_datetime.tzinfo is not None:
                                        scheduled_datetime = datetime.fromtimestamp(scheduled_datetime.timestamp(), pytz.utc)
                                        scheduled_datetime = scheduled_datetime.astimezone(pytz.timezone('Asia/Riyadh')).replace(tzinfo=None)

                                    if unit.startswith("minute"):
                                        adjusted_reminder = scheduled_datetime - timedelta(minutes=amount)
                                    elif unit.startswith("hour"):
                                        adjusted_reminder = scheduled_datetime - timedelta(hours=amount)
                                    elif unit.startswith("day"):
                                        adjusted_reminder = scheduled_datetime - timedelta(days=amount)
                                    else:
                                        adjusted_reminder = reminder_datetime
                                else:
                                    adjusted_reminder = reminder_datetime

                            timezone = pytz.timezone('Asia/Riyadh')
                            now = datetime.now(timezone)

                            if adjusted_reminder.tzinfo is None:
                                adjusted_reminder = timezone.localize(adjusted_reminder)


                            if adjusted_reminder < now:
                                doc_ref.update({
                            "response": f"⚠️ Oops! I can't set a reminder for **{adjusted_reminder.strftime('%Y-%m-%d %I:%M %p')}** because that time has already passed.Try a time that's still in the future ⏰!"
                                })
                                return


                            task_ref.update({'reminder': adjusted_reminder})
                            doc_ref.update({
                                "response": f"✅ Reminder set for **{task_title}** at {adjusted_reminder.strftime('%Y-%m-%d %H:%M')}!",
                                "actionSuggestion": None,
                                "pendingTaskTitle": firestore.DELETE_FIELD
                            })
                            return

                        # elif len(tasks) > 1:
                        #     # If multiple tasks matched, ask user to pick
                        #     options_list = []
                        #     for idx, task in enumerate(tasks, 1):
                        #         raw_date = task.get('scheduledDate')
                        #         date_obj = None

                        #         if isinstance(raw_date, datetime):
                        #             date_obj = raw_date
                        #         elif isinstance(raw_date, str):
                        #             try:
                        #                 date_obj = dateparser.parse(raw_date)
                        #             except:
                        #                 pass

                        #         date_display = date_obj.strftime('%Y-%m-%d') if date_obj else "Unknown Date"


                        #         options_list.append(f"{idx}. {task['title']} (📅 {date_display})")

                        #     response_text = f"**I found multiple tasks named '{task_title}':**\n\n" + "\n".join(options_list) + "\n\n👉 **Please reply with the number of the task you mean.**"

                        #     doc_ref.update({
                        #         "response": response_text,
                        #         "actionSuggestion": "awaiting_reminder_choice",
                        #         "pendingReminderOptions": [
                        #             {"taskID": t["id"], "scheduledDate": t.get("scheduledDate")} for t in tasks
                        #         ],
                        #         "extractedReminderTime": reminder_datetime.isoformat()

                        #     })
                        #     return

                    else:
                        # Couldn't parse reminder time properly
                        doc_ref.update({
                            "response": "⏰ I couldn’t understand the reminder time. Could you please say it like 'tomorrow at 5PM' or '1 hour before'?"
                        })
                        return



            chat_history = []
            try:
                chat_history_ref = db.collection("ChatBot").where("userID", "==", user_id).order_by("timestamp", direction=firestore.Query.ASCENDING)
                history_docs = chat_history_ref.stream()
                for doc in reversed(list(history_docs)):
                    msg = doc.to_dict()
                    role = "assistant" if msg.get("response") else "user"
                    content = msg.get("response") if role == "assistant" else msg.get("message")
                    if content:
                        chat_history.append({"role": role, "content": content})
            except Exception as e:
                print(f"⚠️ Failed to fetch chat history: {e}")

            try:
                messages = [{"role": "system", "content": style_instruction + system_instruction}] + chat_history + [
                    {"role": "user", "content": user_message}
                ]
                response = client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    max_tokens=900,
                    temperature=0.85,
                    presence_penalty=0.4,
                    stream=False
                )
                full_response = response.choices[0].message.content
                if is_valid_utf(full_response):
                     doc_ref.update({"response": sanitize_text(full_response)})

                else:
                    fallback_msg = "⚠️ Sorry, the message contained special characters and couldn't be displayed."
                    doc_ref.update({"response": fallback_msg})
                    print("⚠️ Skipped invalid UTF-16 response and sent fallback message.")
                print(f"✅ Responded to {user_id}: {full_response}")
            except Exception as e:
                print(f"⚠️ Error generating GPT response: {e}")


def handle_delete_task(user_id, user_message, doc_ref):
    try:
        print("🧹 Handling flexible task deletion with preview")

        # 🛠️ Updated extractor prompt to include status
        extract_info_prompt = f"""
You are a JSON extraction tool.

Extract ONLY the following fields from the user's input and return as valid JSON:
- title: the task title if mentioned
- date: the date if mentioned, converted to yyyy-mm-dd format
- status: "completed", "pending", or "uncompleted" if mentioned (lowercase)

If not found, return null.

FORMAT:
{{ "title": "Buy milk", "date": "2025-04-15", "status": "pending" }}
or
{{ "title": null, "date": null, "status": "uncompleted" }}

User: "{user_message}"
"""


        info_response = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "system", "content": extract_info_prompt}],
            max_tokens=100,
            temperature=0.2,
        )

        raw_json = info_response.choices[0].message.content.strip()
        print("📦 Raw output from model:", raw_json)

        if not raw_json.startswith("{"):
            raise ValueError("Invalid JSON received from model")

        parsed_info = json.loads(raw_json)
        title = parsed_info.get("title")
        status = parsed_info.get("status")  # 🆕 New extraction for status!

        # ✅ Extract dates
        extracted_dates = extract_dates_from_message(user_message)
        date_str = extracted_dates[0] if extracted_dates else parsed_info.get("date")

        # ✋ If nothing is extracted
        if not title and not date_str and not status:
            doc_ref.update({
"response": """
🗑️ Sure! I can help you delete a task 😊  
Could you tell me:
- 📝 The task title?  
- 📅 Or the date it’s scheduled for?
- ✅ Or whether it’s completed or pending?
""",
            "actionSuggestion": "awaiting_delete_info"
            })
            return

        # 🧠 Build query
        query = db.collection("Task").where("userID", "==", user_id)

        if title:
            query = query.where("title", "==", title)

        if date_str:
            parsed_date = dateparser.parse(date_str)
            if not parsed_date:
                doc_ref.update({"response": "❗ Couldn’t parse the date you gave me. Try again?"})
                return
            start = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 0, 0, 0)
            end = datetime(parsed_date.year, parsed_date.month, parsed_date.day, 23, 59, 59)
            print(f"📆 Filtering tasks between: {start} and {end}")
            query = query.where("scheduledDate", ">=", start).where("scheduledDate", "<=", end)

        if status:
            if status == "completed":
                query = query.where("completionStatus", "==", 2)
            elif status == "pending":
                query = query.where("completionStatus", "==", 1)
            elif status == "uncompleted":
                query = query.where("completionStatus", "==", 0)


        # 🗑️ Fetch matching tasks
        tasks = list(query.stream())

        if not tasks:
            doc_ref.update({"response": f"😔 I couldn’t find any tasks matching your request."})
            return

        # 🎯 Build preview
        previews = []
        for t in tasks:
            task_data = t.to_dict()
            task_text = format_task(task_data, t.id)
            delete_button = f"DELETE_ID::{t.id}"
            previews.append(task_text + "\n" + delete_button)

        final_message = "🗑️ Here are the matching tasks:\n\n" + "\n\n".join(previews)

        doc_ref.update({
            "response": final_message,
            "actionType": "Delete Task",
            "actionSuggestion": None  # ✅ Clear previous suggestions
        })

        print("✅ Preview sent with delete links.")

    except Exception as e:
        print(f"❌ Error in handle_delete_task: {e}")
        doc_ref.update({
            "response": "⚠️ Sorry, something went wrong while preparing the task deletion."
        })






def handle_add_reminder(user_id, user_message, doc_ref):
    try:
        # Step 1: Extract title and time
        task_title, reminder_datetime, is_relative, has_time = extract_task_title_and_time(user_message)

        if is_relative:
            # Get the task’s scheduled time from Firestore
            matched_tasks = [...]  # already fetched
            if matched_tasks:
                scheduled_dt = matched_tasks[0].get("scheduledDate")
                if isinstance(scheduled_dt, datetime):
                    amount, unit = parse_relative_amount_unit(user_message)
                    if amount and unit:
                        delta = timedelta(**{unit.rstrip('s'): amount})
                        reminder_datetime = scheduled_dt - delta

        # Step 2: If no task title, ask for clarification
        if not task_title:
            doc_ref.update({
                "response": "❓ I couldn't understand which task you want to set a reminder for. Could you clarify?"
            })
            return

        # Step 3: If no reminder time, ask for it (but task is extracted)
        if task_title and (not reminder_datetime or (reminder_datetime and not has_time and not is_relative)):
            doc_ref.update({
                "response": f"⏰ Got it! You want to set a reminder for **{task_title}**. When would you like to be reminded?",
                "actionSuggestion": "awaiting_reminder_time",
                "pendingTaskTitle": task_title
            })
            return

        # Step 4: Search for the task(s)
        tasks_query = db.collection('Task').where('userID', '==', user_id).where('title', '==', task_title).stream()
        matched_tasks = [{"id": doc.id, **doc.to_dict()} for doc in tasks_query]

        if not matched_tasks:
            doc_ref.update({
                "response": f"😕 I couldn't find a task called '{task_title}'. Could you double-check the name?"
            })
            return

        # Step 5: If exactly one match, set the reminder
        if len(matched_tasks) == 1:
            task = matched_tasks[0]
            task_ref = db.collection('Task').document(task['id'])
            scheduled_datetime = task.get('scheduledDate')

            timezone = pytz.timezone('Asia/Riyadh')
            now = datetime.now(timezone)

            if user_requested_relative_time(user_message):
                amount, unit = parse_relative_amount_unit(user_message)

                if isinstance(scheduled_datetime, datetime) and amount:
                    # 🔧 Normalize timezone to naive datetime first
                    if scheduled_datetime.tzinfo is not None:
                        scheduled_datetime = datetime.fromtimestamp(scheduled_datetime.timestamp(), pytz.utc)
                        scheduled_datetime = scheduled_datetime.astimezone(pytz.timezone('Asia/Riyadh')).replace(tzinfo=None)
                    if unit.startswith("minute"):
                        adjusted_reminder = scheduled_datetime - timedelta(minutes=amount)
                    elif unit.startswith("hour"):
                        adjusted_reminder = scheduled_datetime - timedelta(hours=amount)
                    elif unit.startswith("day"):
                        adjusted_reminder = scheduled_datetime - timedelta(days=amount)
                    else:
                        adjusted_reminder = scheduled_datetime  # fallback
                else:
                    adjusted_reminder = scheduled_datetime  # fallback
            else:
                adjusted_reminder = reminder_datetime


            if adjusted_reminder.tzinfo is None:
                adjusted_reminder = timezone.localize(adjusted_reminder)

            if adjusted_reminder < now:
                doc_ref.update({
                    "response": f"⚠️ Oops! I can't set a reminder for **{adjusted_reminder.strftime('%Y-%m-%d %I:%M %p')}** because that time has already passed. Try a time that's still in the future ⏰!"

                })
                return

            task_ref.update({'reminder': adjusted_reminder})
            doc_ref.update({
                "response": f"✅ Reminder set for task **{task_title}** at {adjusted_reminder.strftime('%Y-%m-%d %H:%M')}!"
            })
            return


        # Step 6: If multiple matches, ask user to pick one
        elif len(matched_tasks) > 1:
            previews = []
            for t in matched_tasks:
                task_data = t  # Already a dict
                task_id = t["id"]
                task_text = format_task(task_data, task_id)
                reminder_line = f"REMINDER_ID::{task_id}::{reminder_datetime.isoformat()}"
                previews.append(task_text + "\n" + reminder_line)

            final_message = "🔔 Please choose the task you want to set the reminder for:\n\n" + "\n\n".join(previews)

            doc_ref.update({
                "response": final_message,
                "actionSuggestion": "awaiting_reminder_choice"
            })
            return

    except Exception as e:
        print(f"❌ Error in handle_add_reminder: {e}")
        doc_ref.update({
            "response": "😔 Sorry, something went wrong while setting your reminder."
        })


def handle_reminder_choice(user_id, user_message, doc_ref, pending_options, extracted_time, last_messages):
    try:
        if user_message.startswith("REMINDER_ID::"):
            parts = user_message.replace("REMINDER_ID::", "").split("::")
            task_id = parts[0]
            reminder_time = datetime.fromisoformat(parts[1])

            task_doc = db.collection("Task").document(task_id).get().to_dict()
            scheduled_datetime = task_doc.get("scheduledDate")

            if user_requested_relative_time(user_message):
                amount, unit = parse_relative_amount_unit(user_message)
                if isinstance(scheduled_datetime, datetime) and amount:
                    scheduled_datetime = scheduled_datetime.replace(tzinfo=None)
                    if unit.startswith("minute"):
                        adjusted_reminder = scheduled_datetime - timedelta(minutes=amount)
                    elif unit.startswith("hour"):
                        adjusted_reminder = scheduled_datetime - timedelta(hours=amount)
                    elif unit.startswith("day"):
                        adjusted_reminder = scheduled_datetime - timedelta(days=amount)
                    else:
                        adjusted_reminder = reminder_time
                else:
                    adjusted_reminder = reminder_time
            else:
                adjusted_reminder = reminder_time

            now = datetime.now(pytz.timezone('Asia/Riyadh'))
            if adjusted_reminder < now:
                doc_ref.update({
                    "response": f"⚠️ That reminder time ({adjusted_reminder.strftime('%Y-%m-%d %H:%M')}) is in the past. Please choose a time that hasn’t already passed."
                })
                return

            db.collection('Task').document(task_id).update({'reminder': adjusted_reminder})
            doc_ref.update({
                "response": f"✅ Reminder set for task at {adjusted_reminder.strftime('%Y-%m-%d %H:%M')}!",
                "actionSuggestion": None
            })
            return

    except Exception as e:
        print(f"❌ Error in handle_reminder_choice: {e}")
        doc_ref.update({"response": "⚠️ Failed to set the reminder. Please try again."})


def create_steps(task_name: str) -> str:
    """
    Given a task name, ask GPT to break it down into a simple to-do list.
    Returns a string containing the breakdown.
    """

    system_prompt = (
        "You are a friendly and skilled task planning assistant. "
        "Your goal is to help users break big tasks into clear, short to-do list items. "
        "If the task is large, break it down into 5–10 high-level checklist items. "
        "Keep each item short and clean, like a checklist. Maximum 10 items."
    )

    user_prompt = (
        f"The user wants help breaking down the task: '{task_name}'.\n\n"
        "Please turn it into a clean checklist (5–10 steps), using short item titles."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.5,
        )
        reply = response.choices[0].message.content.strip()
        return reply

    except Exception as e:
        print(f"❌ Error generating steps: {e}")
        return "⚠️ Sorry, I couldn't create the task breakdown right now."


def create_breakdown_steps(task_name, steps):
    """
    Creates a list of breakdown steps based on the input.
    """
    breakdown_steps = []
    for idx, step in enumerate(steps):
        breakdown_steps.append({
            "step_number": idx + 1,
            "task_name": task_name,
            "step_description": step.strip(),
            "completed": False,  # This could be updated later to track completion
        })
    return breakdown_steps

def format_breakdown_response(breakdown_steps, estimated_time):
    """
    Formats the breakdown steps into a readable response for the user.
    """
    response = f"Here is the breakdown of your task (Estimated time: {estimated_time}):\n"
    
    for step in breakdown_steps:
        response += f"Step {step['step_number']}: {step['step_description']}\n"
    
    return response

def start_firestore_listener():
    if not globals().get("listener_started"):
        try:
            print("📥 on_snapshot triggered")
            db.collection("ChatBot").on_snapshot(on_snapshot)
            globals()["listener_started"] = True
            print("👂 Listening for new messages...")
        except Exception as e:
            print(f"⚠️ Listener error: {e}")
            globals()["listener_started"] = False

threading.Thread(target=start_firestore_listener, daemon=True).start()

while True:
 time.sleep(5)



# def extract_dates_from_message(message: str):
#     message = message.lower()
#     phrases = re.findall(r"(?:today|tomorrow|yesterday|next\s\w+|last\s\w+|\d{4}-\d{2}-\d{2})", message)
#     extracted = []

#     for phrase in phrases:
#         parsed_date = dateparser.parse(phrase)
#         if parsed_date:
#             date_str = parsed_date.strftime('%Y-%m-%d')
#             if date_str not in extracted:
#                 extracted.append(date_str)

#     range_match = re.search(r"next\s(\d+)\s(day|days|week|weeks)", message)
#     if range_match:
#         num = int(range_match.group(1))
#         unit = range_match.group(2)
#         base = datetime.today()

#         if "week" in unit:
#             for i in range(num * 7):
#                 date = (base + timedelta(days=i)).strftime('%Y-%m-%d')
#                 if date not in extracted:
#                     extracted.append(date)
#         else:
#             for i in range(num):
#                 date = (base + timedelta(days=i)).strftime('%Y-%m-%d')
#                 if date not in extracted:
#                     extracted.append(date)

#     return extracted