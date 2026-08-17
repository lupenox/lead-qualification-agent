from schemas import InboundLead
from services.scraper import _robots_allows, extract_profile


HTML = """
<html>
  <head>
    <title>Northline Logistics | Freight OS</title>
    <meta name="description" content="SaaS platform for mid-market freight brokers." />
  </head>
  <body>
    <h1>Freight software for growing brokerages</h1>
    <h2>Pricing</h2>
    <p>Book a demo. SOC 2 Type II. Native HubSpot and Salesforce integrations.</p>
    <p>Contact  sales@northline.example</p>
  </body>
</html>
"""


def test_extract_profile_pulls_signals_and_email():
    lead = InboundLead(company_name="Northline Logistics", website="https://northline.example")
    profile = extract_profile(HTML, lead)
    assert profile.scrape_ok
    assert "Freight OS" in profile.title
    assert "pricing_page" in profile.buying_signals
    assert "security" in profile.buying_signals
    assert "hubspot" in profile.technologies
    assert "sales@northline.example" in profile.emails


def test_robots_disallow_matches_path():
    robots = "User-agent: *\nDisallow: /private\n"
    assert _robots_allows(robots, "LeadQualificationAgent/1.0", "/private/home") is False
    assert _robots_allows(robots, "LeadQualificationAgent/1.0", "/") is True
