# Set up your own digital twin

Version 3 • 24 September 2026 • Staff setup guide

A digital twin is an AI helper that learns your role, goals and work style. Think of it as your own Chief of Staff. It can help you plan your day, draft replies, prepare for meetings and track tasks. You still make the decisions.

You need **two things**: the starter TXT tells the AI how to help; app connections let it read your work. Uploading the TXT alone does not connect Slack, email or Drive.

## 1. Choose your work AI

Use an AI tool and account that Pomelo allows for work. The button names below are for **ChatGPT Work**. Other AIs may use different names or may not support every app. If unsure, tell the AI its app name and whether you use a browser, computer app or phone. Ask it to check the current setup steps.

## 2. Find the app connections

In ChatGPT, open **Plugins** in the left sidebar. Search for a service, open its page and select the **plus button** to install it. Complete the connection when asked; some plugins ask when you first use them. A plugin is a link between your AI and another app. [Official setup steps](https://learn.chatgpt.com/docs/plugins).

## 3. Connect each app

Do these one at a time. Sign in on the service's own sign-in page. Never paste a password or sign-in code into the AI chat.

| App | What to do after finding its plugin |
| --- | --- |
| **Slack** | Install Slack → follow the connection prompt → sign in with your work Slack account → check that it is the Pomelo workspace → review the access request and finish. |
| **Gmail** | Install Gmail → follow the connection prompt → choose your **@pomelofashion.com** Google account → review the access request and finish. |
| **Google Drive** | Install Google Drive → follow its connection prompt → choose your work Google account → review the access request and finish. Do this even if Gmail is already connected. |
| **Google Calendar** | If you want meeting help, install Google Calendar → connect your work Google account → review the access request → check which calendar it can read. |

Start with reading and drafting. Tell the AI to wait for your OK before sending messages or changing anything. This is a work rule; it does not change the permissions shown on the sign-in screen.

If an app is missing or says it needs admin approval, ask IT about approved access. Do not switch to a personal account to get around a work limit. You can use the copy-and-paste steps in section 6 while you wait.

## 4. Start your Chief of Staff chat

After installing the plugins, start a new chat or project called **My Chief of Staff**. Upload **Pomelo-Chief-of-Staff-Starter.txt** and type:

> Use this file to set up my Chief of Staff. First help me test my app connections. Then ask me one question at a time about my work. Read and draft only for now.

If your AI cannot read TXT files, open the file and paste its text into the chat.

## 5. Bring real messages and files into the AI

Connecting an app lets the AI use that app when a task needs it. You still need to say **what to read**. In ChatGPT, type **@** and choose the app from the list. [Using connected tools](https://learn.chatgpt.com/docs/get-started-with-work).

Try these tests, one at a time. Replace the words in brackets:

| Source | Copy this prompt |
| --- | --- |
| **Slack** | “Use Slack to read this thread: [message link]. Tell me the latest reply and any task for me. Include the source link. Do not reply.” |
| **Gmail** | “Use Gmail to find the email from [sender] with subject [subject], sent on [date], in my work account. Read the conversation and tell me what I need to do. Include a link. Do not send anything.” |
| **Drive** | “Use Google Drive to read this file: [file link]. Tell me its title and three main points. Do not edit it.” |
| **Calendar** | “Use Google Calendar to show my next two meetings, with dates and times in [time zone]. Do not change them.” |

Check each answer against the real app. A source link and matching content are better proof than the AI saying “connected.” Ask it to name anything it could not read. A successful channel test does not prove it can read every private channel or DM.

## 6. If an app cannot connect, share just what is needed

You can still use your Chief of Staff:

- **Slack:** Open the relevant thread. Copy the messages and replies you need. Paste them into the AI with names, dates and a message link if available. Say: “Summarize this and add my tasks.”
- **Email:** Open the conversation. Copy the sender, date, subject and relevant text into the AI. Add any needed attachment using the AI's upload button. Say: “Tell me what needs a reply and draft one.”
- **Drive:** If allowed, download the needed file and upload it to the AI, or paste the relevant section. Include the file name and date.

A link alone may not give access. An uploaded file is a copy from that moment; upload a fresh copy when it changes. Use only material you are allowed to share. There is no general “email my AI” address—do not forward work email to an address unless your AI service and IT have set it up.

## 7. Tell it what matters to you

Give short answers about your role, top three goals, current tasks, deadlines and key people. Share two messages you wrote so it can learn your style.

Then say:

> Show my Work Profile and Task List. Include the apps that passed the tests and the channels, people and Drive files relevant to my role. Mark anything missing.

## 8. Get a useful daily update

Start by asking for updates when you need them:

> Give me a morning update. Read new items since [date and time] in [Slack channels/DMs], relevant work email and [Drive files/folders]. Check my calendar for today, the next two days and this week. Show what changed, what needs me and what is coming up. Link each key item. Say what you could not check. Save where you stopped so you do not repeat old items.

For the first test, use one day and a small set of sources. Later, say “since our last update.” Ask for an end-of-day update in the same chat.

**How do new messages reach the AI?**

| Method | How it works |
| --- | --- |
| **Ask for an update** | You send a prompt. The AI fetches relevant items through the tested connections, or uses text/files you supplied. Start here. |
| **Timed updates** | Optional. Ask the AI whether its scheduling feature can use your connected apps. If supported, choose one daily time first, name the sources and agree a small usage budget. Create and test one combined update. |
| **Live alerts** | Follow sections F–G in the starter TXT for built-in alerts or IT-assisted setup, delivery tests and stopping alerts. Uploading the TXT alone does not enable them. |

Do not assume a connected app means the AI is watching all day. Do not set checks every few minutes or add a separate checker for every conversation. Scheduled or live delivery is working only after a real update has arrived and the source has been checked.

## 9. Keep your setup

Use the same chat or project. Tell the AI when goals and tasks change. Type **“Save my work profile and task list”** to get a dated file, including the sources tested and the last checks. Keep it for a new chat or another AI. You may need to connect and test the apps again there.

Your first setup is done when each needed source has passed a test, your Work Profile looks right and your first update is useful. Mark blocked sources clearly and use the manual route for them.
