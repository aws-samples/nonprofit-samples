"""Lex V2 fulfillment Lambda for Connect for Good nonprofit chatbot.
Handles intents: DonationStatus, MakeADonation, VolunteerSignup, FAQ, FallbackIntent
"""
import json
import os
import random
import string
from datetime import datetime, timedelta

def handler(event, context):
    intent = event["sessionState"]["intent"]["name"]
    slots = event["sessionState"]["intent"].get("slots", {})

    if intent == "DonationStatus":
        return donation_status(event, slots)
    elif intent == "MakeADonation":
        return make_donation(event, slots)
    elif intent == "VolunteerSignup":
        return volunteer_signup(event, slots)
    elif intent == "FAQ":
        return faq(event, slots)
    elif intent == "MembershipInquiry":
        return membership_inquiry(event, slots)
    else:
        return close(event, intent, "Fulfilled",
                     "I'm sorry, I didn't understand that. Let me transfer you to an agent.")

def donation_status(event, slots):
    donor_id = get_slot(slots, "DonorId")
    if not donor_id:
        return elicit_slot(event, "DonationStatus", "DonorId",
                           "I'd be happy to look up your donation. Could you provide your donor ID or the email address associated with your account?")
    # Simulated lookup
    conf_num = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    date = (datetime.now() - timedelta(days=random.randint(1, 30))).strftime("%B %d, %Y")
    amount = random.choice(["$50", "$100", "$250", "$500"])
    msg = (f"I found your account. Your most recent donation of {amount} was received on {date}. "
           f"Confirmation number: {conf_num}. Your donation is tax-deductible. "
           "Is there anything else I can help you with?")
    return elicit_intent(event, msg)

def make_donation(event, slots):
    amount = get_slot(slots, "DonationAmount")
    if not amount:
        return elicit_slot(event, "MakeADonation", "DonationAmount",
                           "Thank you for your generosity! How much would you like to donate today?")
    msg = (f"Thank you! I'll transfer you to our secure payment line to complete your ${amount} donation. "
           "Your payment information is handled in a PCI-DSS compliant environment. Transferring now...")
    return close(event, "MakeADonation", "Fulfilled", msg)

def volunteer_signup(event, slots):
    name = get_slot(slots, "VolunteerName")
    if not name:
        return elicit_slot(event, "VolunteerSignup", "VolunteerName",
                           "We'd love to have you volunteer! What is your name?")
    program = get_slot(slots, "ProgramArea")
    if not program:
        return elicit_slot(event, "VolunteerSignup", "ProgramArea",
                           f"Great, {name}! Which program area interests you? We have: Food Distribution, Youth Mentoring, Disaster Relief, and Administrative Support.")
    msg = (f"Wonderful, {name}! I've registered your interest in {program}. "
           "A volunteer coordinator will reach out within 24 hours to discuss scheduling. "
           "Thank you for making a difference! Is there anything else I can help you with?")
    return elicit_intent(event, msg)

def faq(event, slots):
    topic = get_slot(slots, "FAQTopic")
    answers = {
        "hours": "Our offices are open Monday through Friday, 8 AM to 6 PM Eastern. Our crisis helpline is available 24/7.",
        "tax": "All donations are tax-deductible. You'll receive a receipt via email within 24 hours of your donation. For annual statements, please contact donor services.",
        "location": "We have offices in all 50 states. Visit our website to find the location nearest you.",
        "matching": "Many employers offer donation matching programs. Check with your HR department, or we can help verify if your company participates.",
    }
    # Keyword mapping for fuzzy matching
    keyword_map = {
        "hours": ["hours", "hour", "open", "time", "schedule", "office"],
        "tax": ["tax", "deduct", "deduction", "receipt", "write-off", "writeoff", "501"],
        "location": ["location", "address", "office", "where", "find", "near", "directions"],
        "matching": ["match", "matching", "employer", "corporate", "company"],
    }
    if not topic:
        return elicit_slot(event, "FAQ", "FAQTopic",
                           "I can help with frequently asked questions about: office hours, tax deductions, locations, or employer matching. What would you like to know?")
    topic_lower = topic.lower()
    # Try exact match first
    msg = answers.get(topic_lower)
    if not msg:
        # Try keyword matching
        for key, keywords in keyword_map.items():
            if any(kw in topic_lower for kw in keywords):
                msg = answers[key]
                break
    if not msg:
        msg = "I don't have specific information on that topic, but I can connect you with an agent. Just say 'transfer me to an agent'."
    return elicit_intent(event, msg + " Is there anything else I can help you with?")

def membership_inquiry(event, slots):
    topic = get_slot(slots, "MembershipTopic")
    answers = {
        "tiers": "We offer four membership tiers: Friend ($25/year), Supporter ($75/year), Champion ($250/year), and Patron ($1,000/year). Each tier includes increasing benefits like event access, discounts, and recognition.",
        "benefits": "Member benefits include a monthly newsletter, exclusive event invitations, gift shop discounts, early access to volunteer opportunities, and an annual impact report. Higher tiers include VIP events and named program sponsorship.",
        "renewal": "Memberships are valid for 12 months. You can renew online, by phone, or by chat. Renewal reminders are sent 30 days before expiration. Would you like me to connect you with our membership team to renew?",
        "cancel": "Memberships can be cancelled at any time by contacting Member Services. Refunds are prorated based on remaining months. Would you like me to transfer you to our membership team?",
        "cost": "Membership starts at $25/year for our Friend tier. We also offer Supporter ($75), Champion ($250), and Patron ($1,000) tiers with increasing benefits.",
        "join": "You can join online at our website, by phone, or through chat. Just choose your tier and complete the signup. Would you like me to connect you with our membership team to get started?",
    }
    keyword_map = {
        "tiers": ["tier", "tiers", "level", "levels", "types", "options", "plans"],
        "benefits": ["benefit", "benefits", "perks", "get", "include", "what do"],
        "renewal": ["renew", "renewal", "extend", "continue"],
        "cancel": ["cancel", "cancellation", "stop", "end", "quit"],
        "cost": ["cost", "price", "pricing", "how much", "fee", "fees", "pay"],
        "join": ["join", "sign up", "signup", "become", "register", "enroll"],
    }
    if not topic:
        return elicit_slot(event, "MembershipInquiry", "MembershipTopic",
                           "I can help with membership tiers and pricing, renewals, cancellations, or benefits. What would you like to know?")
    topic_lower = topic.lower()
    msg = answers.get(topic_lower)
    if not msg:
        for key, keywords in keyword_map.items():
            if any(kw in topic_lower for kw in keywords):
                msg = answers[key]
                break
    if not msg:
        msg = "I can connect you with our membership team for more details on that."
    return elicit_intent(event, msg + " Is there anything else I can help you with?")

# ── Lex response helpers ──

def get_slot(slots, name):
    slot = slots.get(name)
    if slot and slot.get("value"):
        return slot["value"].get("interpretedValue") or slot["value"].get("originalValue")
    return None

def close(event, intent_name, state, message):
    """Ends the Lex session and returns control to the Connect flow."""
    return {
        "sessionState": {
            "dialogAction": {"type": "Close"},
            "intent": {"name": intent_name, "state": state},
        },
        "messages": [{"contentType": "PlainText", "content": message}],
    }

def elicit_intent(event, message):
    """Keeps the Lex session alive and prompts for the next intent."""
    return {
        "sessionState": {
            "dialogAction": {"type": "ElicitIntent"},
        },
        "messages": [{"contentType": "PlainText", "content": message}],
    }

def elicit_slot(event, intent_name, slot_name, message):
    return {
        "sessionState": {
            "dialogAction": {"type": "ElicitSlot", "slotToElicit": slot_name},
            "intent": {
                "name": intent_name,
                "state": "InProgress",
                "slots": event["sessionState"]["intent"].get("slots", {}),
            },
        },
        "messages": [{"contentType": "PlainText", "content": message}],
    }
