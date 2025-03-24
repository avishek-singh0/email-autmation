import time
import base64
import os
import google.generativeai as genai
import supabase
import re
from email.mime.text import MIMEText
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()



genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-pro-latest")


supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

supabase_client = supabase.create_client(supabase_url, supabase_key)

# Gmail API scopes
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

def authenticate_gmail_api():
    """Authenticate and return Gmail API service."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

def fetch_latest_unread_email(service):
    """Fetch the most recent unread email from Gmail."""
    try:
        result = service.users().messages().list(
            userId="me", labelIds=["INBOX"], q="is:unread", maxResults=1
        ).execute()

        messages = result.get("messages", [])

        if not messages:
            return None  # No new emails

        msg_id = messages[0]["id"]
        email_data = service.users().messages().get(userId="me", id=msg_id).execute()
        headers = email_data["payload"]["headers"]

        subject = next((header["value"] for header in headers if header["name"] == "Subject"), "No Subject")
        sender = next((header["value"] for header in headers if header["name"] == "From"), "Unknown Sender")

        # Extract email body
        email_text = "No Content"
        if "parts" in email_data["payload"]:
            for part in email_data["payload"]["parts"]:
                if part["mimeType"] == "text/plain" and "data" in part["body"]:
                    email_text = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")
                    break

        return msg_id, sender, subject, email_text

    except Exception as e:
        print("❌ Error fetching latest email:", e)
        return None

def check_if_existing_customer(email):
    """Check if sender exists in Supabase database."""
 

    try:
        response = supabase_client.table("customers").select("email").eq("email", email).execute()
        # print(f"🔍 Extracted Email for Database Check:,{email}")
        return len(response.data) > 0  # Returns True if email exists
       
    except Exception as e:
        print("❌ Supabase Query Error:", e)
        return False

def generate_ai_response(email_text):
    """Generate AI response using Gemini AI."""
    try:
        response = model.generate_content(f"Summarize this email and provide response :\n{email_text}")
        return response.text
    except Exception as e:
        print("❌ Error generating response:", e)
        return "I'm sorry, but I couldn't process your request."

def send_email_reply(service, recipient_email, subject, reply_text):
    """Send an email reply via Gmail API."""
    try:
        message = MIMEText(reply_text)
        message["to"] = recipient_email
        message["subject"] = f"Re: {subject}"

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw_message}).execute()

        print(f"✅ Reply sent to {recipient_email}")

    except Exception as e:
        print("❌ Error sending email:", e)

def mark_email_as_read(service, msg_id):
    """Mark email as read."""
    try:
        service.users().messages().modify(userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}).execute()
    except Exception as e:
        print("❌ Error marking email as read:", e)

def extract_email(sender):
    match = re.search(r"<(.*?)>", sender)  # Extracts text inside <>
    return match.group(1) if match else sender.strip()  # Returns only email

def handle_enquiry(service, sender_email, subject):
    """Send an introductory email for enquiry emails."""
    intro_message = (
        f"Hello,\n\n"
        "Thank you for reaching out! We are [Your Company Name], a leader in [Industry/Niche].\n\n"
        "We'd love to discuss how we can help. Can we set up a quick introductory meeting?\n\n"
        "Let us know a time that works for you!\n\n"
        "Best Regards,\n[Your Name]\n[Your Company Name]"
    )

    send_email_reply(service, sender_email, subject, intro_message)
    print(f"📧 Enquiry email handled. Sent introductory message to {sender_email}.")

def is_enquiry_email(subject, email_text):
    """Check if the email is an enquiry based on subject or content."""
    enquiry_keywords = ["enquiry", "information", "pricing", "quote", "partnership", "collaboration", "services"]
    subject_lower = subject.lower()
    email_text_lower = email_text.lower()

    return any(keyword in subject_lower or keyword in email_text_lower for keyword in enquiry_keywords)

def check_and_follow_up_leads(service):
    """Check Supabase for leads needing follow-up."""
    try:
        followups = supabase_client.table("leads").select("email", "last_followup").execute()
        for lead in followups.data:
            email, last_followup = lead["email"], lead["last_followup"]
            
            if last_followup and datetime.strptime(last_followup, "%Y-%m-%d") < datetime.now() - timedelta(days=3):
                send_email_reply(service, email, "Following up on your inquiry", "Just checking if you had any questions.")
                supabase_client.table("leads").update({"last_followup": datetime.now().strftime("%Y-%m-%d")}).eq("email", email).execute()
    except Exception as e:
        print("❌ Error in follow-up process:", e)


def main():
    """Main function to automate email responses for existing customers."""
    service = authenticate_gmail_api()
    
    print("🚀 AI Email Assistant started...")

    while True:
        print("\n🔍 Checking for new unread emails...")
        email = fetch_latest_unread_email(service)

        if email:
            msg_id, sender, subject, email_text = email
            print(f"📩 New email from {sender} - {subject}")
            email_address = extract_email(sender)
            print(f"🔍 Extracted Email for Database Check: {email_address}")

            if check_if_existing_customer(email_address):
                print(f"✅ {email_address} is an existing customer.")

                summary = generate_ai_response(email_text)
                print("\n🔹 Email Summary:", summary)

                # Provide response options
                print("\n💬 Choose a response option:")
                print("1️⃣ Short Response")
                print("2️⃣ Detailed Response")
                print("3️⃣ Request a Meeting")
                print("4️⃣ Custom Reply")

                choice = input("Enter option (1-4): ").strip()
                if choice == "1":
                    reply_text = f"Thank you for reaching out. We acknowledge your email and will get back to you soon."
                    # print(reply_text)
                elif choice == "2":
                    reply_text = f"Hello,\n\n{summary}\n\nLet me know how we can assist further."
                    # print(reply_text)
                elif choice == "3":
                    reply_text = "We'd love to discuss this further. Can we schedule a meeting?"
                    # print(reply_text)
                else:
                    reply_text = input("✍ Enter your custom reply: ")
                
                print("\n📤 Selected Response to Send:")
                print(reply_text)

                send_email_reply(service, sender, subject, reply_text)
                mark_email_as_read(service, msg_id)

            elif is_enquiry_email(subject, email_text):  # 🔍 Check if it's an enquiry
                print(f"🔍 Enquiry detected from {email_address}. Sending introductory email...")
                handle_enquiry(service, email_address, subject)
                mark_email_as_read(service, msg_id)

            else:
                print("❌ Email sender is not an existing customer. Skipping.")
                

        else:
            print("📭 No unread emails found. Retrying in 30 seconds...")

        check_and_follow_up_leads(service)
        time.sleep(30)  # Reduce wait time for faster debugging

if __name__ == "__main__":
    main()
