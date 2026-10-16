"""
Outreach templates and benefit mapping for WhatsApp and Email communication.
Provides templates in English and Hinglish with language-aware category benefits
and graceful degradation for missing metrics.
"""

from typing import Any, Dict, Optional, Tuple

# WhatsApp Templates
TEMPLATES: Dict[str, Dict[str, str]] = {
    "English": {
        "No website": (
            "Hello {name} team, this is {sender}.\n\n"
            "I came across {name} on Google Maps and noticed you do not have an active website yet. "
            "Most customers check online before visiting, so businesses without a site often miss out on new inquiries.\n\n"
            "We build clean, fast websites with direct WhatsApp booking. "
            "Could I share a quick 2-minute preview of how it would look?"
        ),
        "Website redesign": (
            "Hello {name} team, this is {sender}.\n\n"
            "I reviewed your website and noticed an issue: {problem}. "
            "Since most visitors browse on smartphones today, this could lead to lost inquiries.\n\n"
            "We fix these issues quickly without rebuilding your entire website from scratch. "
            "Would you be open to a quick, free audit report?"
        ),
        "WhatsApp API": (
            "Hello {name} team, this is {sender}.\n\n"
            "We set up WhatsApp Business automation for businesses like yours to handle "
            "{benefit} automatically from your own WhatsApp number.\n\n"
            "Could I share a quick 2-minute demo? Absolutely no pressure if you are not interested right now."
        ),
        "Follow-up (after 3 days)": (
            "Hello {name} team, just following up on my previous message.\n\n"
            "No worries at all if you are busy. Just wanted to see if upgrading your online presence "
            "or WhatsApp workflow is on your radar this month.\n\n"
            "Best regards,\n{sender}"
        ),
        "Final nudge (after 7 days)": (
            "Hello {name} team, one last check-in from my side.\n\n"
            "I understand you might have other priorities right now, so I will not follow up again. "
            "If you ever need help with web design or WhatsApp automation, feel free to reach out anytime!\n\n"
            "Best regards,\n{sender}"
        ),
    },
    "Hinglish": {
        "No website": (
            "Namaste {name} team, main {sender} se.\n\n"
            "Maine Google par {name} dekha, par online koi active website nahi mili. "
            "Aaj kal zyadatar customers pehle online search karte hain aur website na milne par "
            "dusre option par chale jaate hain.\n\n"
            "Hum local businesses ke liye clean aur fast website banate hain (direct WhatsApp button ke saath). "
            "Kya main 2 minute mein ek sample preview share karun?"
        ),
        "Website redesign": (
            "Namaste {name} team, main {sender} se.\n\n"
            "Aapki website dekhi aur ek issue notice kiya: {problem}.\n"
            "Zyadatar log mobile se browse karte hain, isliye kai enquiries miss ho sakti hain.\n\n"
            "Hum ise bina poori site dobara banaye aasani se fix kar sakte hain. "
            "Kya main aapko ek free quick audit report bhej dun?"
        ),
        "WhatsApp API": (
            "Namaste {name} team, main {sender} se.\n\n"
            "Hum local businesses ke liye WhatsApp Business platform set up karte hain, "
            "jisse {benefit} aapke apne WhatsApp number se automatic ho jaata hai.\n\n"
            "Kya main 2 minute ka demo bhej dun? Agar pasand na aaye toh koi baat nahi."
        ),
        "Follow-up (after 3 days)": (
            "Namaste {name} team, pichle message ke regarding follow-up kar raha tha.\n\n"
            "Agar abhi time na ho toh koi baat nahi, bas bata dijiye ki aap abhi is par plan kar rahe hain ya nahi. "
            "Main usi hisaab se connect karunga.\n\n"
            "- {sender}"
        ),
        "Final nudge (after 7 days)": (
            "Namaste {name} team, meri taraf se ek aakhri quick follow-up.\n\n"
            "Main samajh sakta hu aap busy honge, isliye ab dobara follow-up nahi karunga. "
            "Agar future mein website upgrade ya WhatsApp automation ki zaroorat ho, toh aap kabhi bhi connect kar sakte hain.\n\n"
            "- {sender}"
        ),
    },
}

# Email Templates (Plain-text, 80-120 words, soft question, opt-out line, dynamic sender and website)
EMAIL_TEMPLATES: Dict[str, Dict[str, Dict[str, str]]] = {
    "English": {
        "No website": {
            "subject": "Quick question regarding {name}'s online presence",
            "body": (
                "Hi {name} team,\n\n"
                "My name is {sender}. I recently came across {name} on Google Maps while researching local businesses "
                "in your area, and noticed you don't have an active website listed.\n\n"
                "Today, most potential customers search online before visiting or calling. When they can't find an "
                "official website, they often turn to nearby competitors.\n\n"
                "We build modern, fast-loading websites specifically designed for local businesses, complete with "
                "direct WhatsApp inquiries and mobile booking.\n\n"
                "Would you be open to a quick 2-minute preview of what a website could look like for {name}?\n\n"
                "If this isn't relevant, just let me know and I won't write again.\n\n"
                "Best regards,\n"
                "{sender}\n"
                "{website}"
            ),
        },
        "Website audit findings": {
            "subject": "Quick technical observation regarding {site}",
            "body": (
                "Hi {name} team,\n\n"
                "My name is {sender}. I was looking at your website ({site}) and noticed an issue that might be "
                "affecting your visitors: {problem}.{psi_line}\n\n"
                "Since over 70% of local customers browse on mobile devices today, technical issues like this "
                "often lead to visitors dropping off before contacting you.\n\n"
                "We help local businesses resolve these specific UX and performance issues quickly without having "
                "to rebuild the entire website from scratch.\n\n"
                "Would you be open to a free, detailed audit report showing exactly how to fix this?\n\n"
                "If this isn't relevant, just let me know and I won't write again.\n\n"
                "Best regards,\n"
                "{sender}\n"
                "{website}"
            ),
        },
        "WhatsApp API": {
            "subject": "Automated WhatsApp inquiries for {name}",
            "body": (
                "Hi {name} team,\n\n"
                "My name is {sender}. I'm reaching out because we help local businesses automate customer "
                "communication on WhatsApp directly from their official phone numbers.\n\n"
                "For businesses like {name}, this handles {benefit} automatically, 24/7, without requiring staff to "
                "reply manually to repetitive questions.\n\n"
                "This ensures every customer inquiry gets answered in seconds, helping you capture leads who would "
                "otherwise contact competitors.\n\n"
                "Could I share a 2-minute demo link showing how it functions? Absolutely no pressure if this isn't "
                "a priority right now.\n\n"
                "If this isn't relevant, just let me know and I won't write again.\n\n"
                "Best regards,\n"
                "{sender}\n"
                "{website}"
            ),
        },
        "Follow-up (after 3 days)": {
            "subject": "Re: Checking in regarding {name}",
            "body": (
                "Hi {name} team,\n\n"
                "I wanted to follow up briefly on my note from earlier this week. I know you're likely busy managing "
                "day-to-day operations.\n\n"
                "I just wanted to check if improving your online presence or streamlining customer inquiries is "
                "something you're considering this quarter.\n\n"
                "If you have 5 minutes sometime this week, I'd be glad to share some quick ideas tailored for {name}.\n\n"
                "If this isn't relevant, just let me know and I won't write again.\n\n"
                "Best regards,\n"
                "{sender}\n"
                "{website}"
            ),
        },
        "Final nudge (after 7 days)": {
            "subject": "Closing the loop on {name}",
            "body": (
                "Hi {name} team,\n\n"
                "One final note from my side to close the loop. I haven't heard back, so I assume this isn't the right "
                "time, and I promise not to follow up further.\n\n"
                "If you ever decide down the road to revamp your website, fix performance issues, or automate customer "
                "inquiries, feel free to keep my details handy and reach out anytime.\n\n"
                "Wishing {name} continued success.\n\n"
                "If this isn't relevant, just let me know and I won't write again.\n\n"
                "Best regards,\n"
                "{sender}\n"
                "{website}"
            ),
        },
    },
    "Hinglish": {
        "No website": {
            "subject": "{name} ke online presence ke regarding ek quick question",
            "body": (
                "Namaste {name} team,\n\n"
                "Main {sender} baat kar raha hu. Maine recently Google Maps par {name} dekha aur notice kiya ki "
                "aapki koi official website listed nahi hai.\n\n"
                "Aaj kal zyadatar customers call ya visit karne se pehle online check karte hain. Website na milne "
                "par kaafi customers dusre local alternatives par chale jaate hain.\n\n"
                "Hum local businesses ke liye modern, fast websites create karte hain jisme direct WhatsApp chat aur "
                "enquiry buttons integrated hote hain.\n\n"
                "Kya main {name} ke liye ek sample preview share kar sakta hu? Dekhne mein sirf 2 minute lagenge.\n\n"
                "Agar yeh relevant nahi hai, toh bas bata dijiye aur main dobara contact nahi karunga.\n\n"
                "Best regards,\n"
                "{sender}\n"
                "{website}"
            ),
        },
        "Website audit findings": {
            "subject": "{site} ke baare mein ek zaroori observation",
            "body": (
                "Namaste {name} team,\n\n"
                "Main {sender} baat kar raha hu. Maine aapki website ({site}) visit ki aur ek technical issue "
                "notice kiya: {problem}.{psi_line}\n\n"
                "Aaj kal 70% se zyada customers mobile phones se websites browse karte hain, isliye aisi problems ki "
                "wajah se enquiries miss ho jaati hain.\n\n"
                "Hum bina poori website dobara banaye in specific technical issues ko fast resolve karte hain.\n\n"
                "Kya main aapke saath ek free, detailed audit report share karun jisme step-by-step solution likha ho?\n\n"
                "Agar yeh relevant nahi hai, toh bas bata dijiye aur main dobara contact nahi karunga.\n\n"
                "Best regards,\n"
                "{sender}\n"
                "{website}"
            ),
        },
        "WhatsApp API": {
            "subject": "{name} ke liye automated WhatsApp customer inquiry system",
            "body": (
                "Namaste {name} team,\n\n"
                "Main {sender} baat kar raha hu. Hum local businesses ke liye official WhatsApp Business automation "
                "setup karte hain jisse customer communication seamless ho jaaye.\n\n"
                "{name} ke liye yeh {benefit} automatically manage kar deta hai, bina kisi staff ke manual reply ke.\n\n"
                "Isse har customer inquiry ka turant reply milta hai aur response time fast hone se conversions "
                "improve hote hain.\n\n"
                "Kya main ek 2-minute ka quick demo video share karun? Agar pasand na aaye toh koi issue nahi hai.\n\n"
                "Agar yeh relevant nahi hai, toh bas bata dijiye aur main dobara contact nahi karunga.\n\n"
                "Best regards,\n"
                "{sender}\n"
                "{website}"
            ),
        },
        "Follow-up (after 3 days)": {
            "subject": "Re: {name} ke regarding quick follow-up",
            "body": (
                "Namaste {name} team,\n\n"
                "Pichle message ke regarding quick follow-up kar raha tha. Main samajh sakta hu aap business operations "
                "mein busy honge.\n\n"
                "Bas ek baar confirm karna chahta tha ki kya website upgrade ya customer communication improve karna "
                "aapki priority list mein hai.\n\n"
                "Agar aapke paas 5 minute ka time ho, toh main {name} ke liye tailored practical ideas share kar sakta hu.\n\n"
                "Agar yeh relevant nahi hai, toh bas bata dijiye aur main dobara contact nahi karunga.\n\n"
                "Best regards,\n"
                "{sender}\n"
                "{website}"
            ),
        },
        "Final nudge (after 7 days)": {
            "subject": "{name} ke regarding final update",
            "body": (
                "Namaste {name} team,\n\n"
                "Meri taraf se yeh aakhri message hai. Reply na aane par main assume kar raha hu ki abhi yeh aapke liye "
                "right time nahi hai, isliye main aage follow-up nahi karunga.\n\n"
                "Agar future mein kabhi website revamp, speed optimization ya WhatsApp automation par help chahiye ho, "
                "toh aap bejhijhak mujhe contact kar sakte hain.\n\n"
                "{name} ki growth ke liye best wishes.\n\n"
                "Agar yeh relevant nahi hai, toh bas bata dijiye aur main dobara contact nahi karunga.\n\n"
                "Best regards,\n"
                "{sender}\n"
                "{website}"
            ),
        },
    },
}


def api_benefit(category: str, language: str = "English") -> str:
    """Return tailored business automation benefits in English or Hinglish."""
    c = (category or "").lower()
    is_hi = language.lower() == "hinglish"

    if any(k in c for k in ("coaching", "school", "institute", "academy", "tuition", "college", "classes")):
        return (
            "admission enquiries ka auto-reply, fees reminder aur batch updates"
            if is_hi
            else "instant admission inquiries, fee reminders, and batch schedule updates"
        )
    if any(k in c for k in ("clinic", "hospital", "doctor", "dental", "pathology", "lab", "health")):
        return (
            "appointment booking, reminders aur report updates"
            if is_hi
            else "automated appointment bookings, patient reminders, and lab report delivery"
        )
    if any(k in c for k in ("restaurant", "cafe", "bakery", "food", "sweet", "hotel")):
        return (
            "order updates, offers aur repeat customers ko bulk messages"
            if is_hi
            else "order confirmations, menu sharing, and special offers to repeat diners"
        )
    if any(k in c for k in ("real estate", "property", "builder", "realtor")):
        return (
            "enquiry ka turant reply aur site-visit follow-ups"
            if is_hi
            else "instant inquiry responses and site-visit scheduling"
        )
    if any(k in c for k in ("shop", "store", "boutique", "showroom", "fashion", "retail")):
        return (
            "catalog sharing, order updates aur festival broadcast"
            if is_hi
            else "product catalog sharing, order tracking, and seasonal promotional alerts"
        )

    return (
        "customer enquiries ka auto-reply aur follow-up messages"
        if is_hi
        else "automated customer inquiry responses and smart follow-ups"
    )


def format_message(
    template: str,
    name: str,
    sender: str,
    problem: str = "",
    benefit: str = "",
) -> str:
    """Format WhatsApp outreach template string safely without missing key errors."""
    clean_name = (name or "there").strip()
    clean_sender = (sender or "Jitendra, Appnix Technologies").strip()
    clean_problem = (problem or "").strip()
    clean_benefit = (benefit or "").strip()

    return template.format(
        name=clean_name,
        sender=clean_sender,
        problem=clean_problem,
        benefit=clean_benefit,
    )


def format_email(
    language: str,
    template_name: str,
    name: str,
    sender: str,
    site: str = "",
    problem: str = "",
    benefit: str = "",
    psi_score: Any = "",
    website_url: str = "appnix.org",
) -> Tuple[str, str]:
    """
    Format subject and body for an email template.
    Degrades gracefully when metrics like psi_score or site are absent.
    """
    lang_key = "Hinglish" if language.lower() == "hinglish" else "English"
    templates_for_lang = EMAIL_TEMPLATES.get(lang_key, EMAIL_TEMPLATES["English"])
    tpl_entry = templates_for_lang.get(template_name, templates_for_lang["No website"])

    raw_subject = tpl_entry["subject"]
    raw_body = tpl_entry["body"]

    clean_name = (name or "there").strip()
    clean_sender = (sender or "Jitendra, Appnix Technologies").strip()
    clean_site = (site or "your website").strip()
    clean_problem = (problem or "").strip()
    clean_benefit = (benefit or "").strip()

    raw_url = (website_url or "appnix.org").strip()
    clean_website = raw_url if raw_url.startswith(("http://", "https://")) else f"https://{raw_url}"

    # Graceful degradation for psi_score
    psi_str = str(psi_score).strip() if psi_score is not None else ""
    if psi_str and psi_str.isdigit():
        if lang_key == "Hinglish":
            psi_line = f"\nSaath hi, Google mobile speed score abhi {psi_str}/100 show kar raha hai."
        else:
            psi_line = f"\nAdditionally, Google's mobile performance score is currently {psi_str}/100."
    else:
        psi_line = ""

    formatted_subject = raw_subject.format(
        name=clean_name,
        sender=clean_sender,
        site=clean_site,
        problem=clean_problem,
        benefit=clean_benefit,
        website=clean_website,
    )

    formatted_body = raw_body.format(
        name=clean_name,
        sender=clean_sender,
        site=clean_site,
        problem=clean_problem,
        benefit=clean_benefit,
        psi_line=psi_line,
        website=clean_website,
    )

    return formatted_subject, formatted_body
