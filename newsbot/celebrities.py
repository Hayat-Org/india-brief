"""Celebrity / famous-person detection for the fast-track lane.

The AI scorer also returns a `celebrity` name; this list is the deterministic backup. It is used to score
these stories first, to recognise them before the AI has looked at them, and to keep the lane working when
the AI free quota is used up. Politicians, judges, officials and religious leaders are deliberately NOT here:
political and sensitive stories keep their own caps and neutrality rules.

Add names freely (full name as it appears in headlines).
"""
import re

ENTERTAINMENT = [
    "Shah Rukh Khan", "Salman Khan", "Aamir Khan", "Amitabh Bachchan", "Abhishek Bachchan", "Aishwarya Rai",
    "Akshay Kumar", "Ranveer Singh", "Deepika Padukone", "Alia Bhatt", "Ranbir Kapoor", "Priyanka Chopra",
    "Katrina Kaif", "Vicky Kaushal", "Kareena Kapoor", "Saif Ali Khan", "Hrithik Roshan", "Ajay Devgn", "Kajol",
    "Rani Mukerji", "Anushka Sharma", "Kangana Ranaut", "Shahid Kapoor", "Varun Dhawan", "Kartik Aaryan",
    "Sidharth Malhotra", "Kiara Advani", "Shraddha Kapoor", "Tiger Shroff", "Sonam Kapoor", "Madhuri Dixit",
    "Sunny Deol", "Dharmendra", "Anil Kapoor", "Sanjay Dutt", "Govinda", "Mithun Chakraborty", "Jackie Shroff",
    "Ananya Panday", "Janhvi Kapoor", "Sara Ali Khan", "Taapsee Pannu", "Vidya Balan", "Sonakshi Sinha",
    "Pankaj Tripathi", "Nawazuddin Siddiqui", "Rajkummar Rao", "Ayushmann Khurrana", "Bobby Deol",
    "Rajinikanth", "Kamal Haasan", "Thalapathy Vijay", "Prabhas", "Allu Arjun", "Mahesh Babu", "Jr NTR",
    "Ram Charan", "Chiranjeevi", "Rashmika Mandanna", "Samantha Ruth Prabhu", "Nayanthara", "Dhanush",
    "Suriya", "Mohanlal", "Mammootty", "Dulquer Salmaan", "Fahadh Faasil", "Vijay Deverakonda",
    "Diljit Dosanjh", "Arijit Singh", "A R Rahman", "AR Rahman", "Shreya Ghoshal", "Sonu Nigam", "Badshah",
    "Honey Singh", "Neha Kakkar", "Karan Johar", "Rohit Shetty", "SS Rajamouli", "S S Rajamouli",
    "Sanjay Leela Bhansali", "Kapil Sharma", "Zakir Khan", "Bhuvan Bam", "Elvish Yadav", "Chetan Bhagat",
]
SPORTS = [
    "Virat Kohli", "Rohit Sharma", "MS Dhoni", "M S Dhoni", "Mahendra Singh Dhoni", "Sachin Tendulkar",
    "Jasprit Bumrah", "Hardik Pandya", "KL Rahul", "Shubman Gill", "Rishabh Pant", "Ravindra Jadeja",
    "Ravichandran Ashwin", "Suryakumar Yadav", "Yuvraj Singh", "Sourav Ganguly", "Rahul Dravid",
    "Gautam Gambhir", "Yashasvi Jaiswal", "Mohammed Shami", "Mohammed Siraj", "Kuldeep Yadav", "Arshdeep Singh",
    "Kapil Dev", "Sunil Gavaskar", "Smriti Mandhana", "Harmanpreet Kaur", "Mithali Raj", "Neeraj Chopra",
    "PV Sindhu", "P V Sindhu", "Mary Kom", "Sunil Chhetri", "Viswanathan Anand", "Gukesh", "Praggnanandhaa",
    "Saina Nehwal", "Mirabai Chanu", "Manu Bhaker", "Vinesh Phogat", "Sania Mirza", "Rohan Bopanna",
    "Sakshi Malik", "Bajrang Punia", "Lakshya Sen", "Satwiksairaj Rankireddy",
]
BUSINESS = [
    "Mukesh Ambani", "Nita Ambani", "Anant Ambani", "Gautam Adani", "Anand Mahindra", "Azim Premji",
    "Narayana Murthy", "Kumar Mangalam Birla", "Nandan Nilekani", "Sundar Pichai", "Satya Nadella",
    "Nikhil Kamath", "Ritesh Agarwal", "Bhavish Aggarwal", "Vijay Shekhar Sharma", "Aman Gupta",
    "Deepinder Goyal", "Falguni Nayar", "Radhika Merchant", "Natarajan Chandrasekaran",
]
ALIASES = {  # short forms used in headlines -> full name
    "SRK": "Shah Rukh Khan", "Big B": "Amitabh Bachchan", "Kohli": "Virat Kohli", "Dhoni": "MS Dhoni",
    "Tendulkar": "Sachin Tendulkar", "Bumrah": "Jasprit Bumrah", "Thalaiva": "Rajinikanth",
    "Neeraj Chopra": "Neeraj Chopra",
}

_NAMES = {n: n for n in ENTERTAINMENT + SPORTS + BUSINESS}
_NAMES.update(ALIASES)
_PATTERN = re.compile(r"(?<![A-Za-z])(" + "|".join(
    re.escape(n) for n in sorted(_NAMES, key=len, reverse=True)) + r")(?![A-Za-z])", re.I)

DEATH = re.compile(r"\b(dies|died|dead|death|passes away|passed away|demise|no more|last rites|funeral|"
                   r"last breath|critical condition|life support|hospitali[sz]ed|heart attack)\b", re.I)
RUMOUR = re.compile(r"\b(rumou?rs?|reportedly|speculat\w*|sources? (say|claim|reveal)|spotted (with|together)|"
                    r"link-?ups?|dating|affair|netizens|trolled|allegedly (dating|cheat)|unconfirmed)\b", re.I)


def match(text: str) -> str:
    """Full name of the first roster celebrity found in the text, else ''."""
    m = _PATTERN.search(text or "")
    if not m:
        return ""
    found = m.group(1)
    for key, full in _NAMES.items():
        if key.lower() == found.lower():
            return full
    return found


def name_in_text(name: str, text: str) -> bool:
    """True when the person is named: full name, or the surname/last word (e.g. 'Dhoni')."""
    if not name:
        return True
    low = (text or "").lower()
    parts = [p for p in re.findall(r"[A-Za-z]+", name) if len(p) > 2]
    return name.lower() in low or (bool(parts) and parts[-1].lower() in low)


def is_death_story(text: str) -> bool:
    return bool(DEATH.search(text or ""))


def is_rumour(text: str) -> bool:
    return bool(RUMOUR.search(text or ""))
