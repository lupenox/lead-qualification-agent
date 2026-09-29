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


UA = "LeadQualificationAgent/1.0 (+https://github.com/lupenox/lead-qualification-agent)"


def test_robots_empty_disallow_allows_everything():
    assert _robots_allows("User-agent: *\nDisallow:\n", UA, "/") is True
    assert _robots_allows("User-agent: *\nDisallow:\n", UA, "/pricing") is True


def test_robots_longest_match_wins_and_allow_is_honored():
    robots = "User-agent: *\nDisallow: /private\nAllow: /private/press\n"
    assert _robots_allows(robots, UA, "/private/secret") is False
    assert _robots_allows(robots, UA, "/private/press/kit") is True
    # Order does not matter: a broader Allow does not override a longer Disallow.
    robots = "User-agent: *\nAllow: /\nDisallow: /private\n"
    assert _robots_allows(robots, UA, "/") is True
    assert _robots_allows(robots, UA, "/private") is False


def test_robots_allow_wins_ties():
    robots = "User-agent: *\nDisallow: /page\nAllow: /page\n"
    assert _robots_allows(robots, UA, "/page") is True


def test_robots_specific_group_overrides_wildcard():
    robots = (
        "User-agent: *\nDisallow: /\n\n"
        "User-agent: LeadQualificationAgent\nDisallow: /admin\n"
    )
    assert _robots_allows(robots, UA, "/") is True
    assert _robots_allows(robots, UA, "/admin/x") is False
    assert _robots_allows(robots, "OtherBot/2.0", "/") is False


def test_robots_grouped_user_agents_and_wildcards():
    robots = (
        "User-agent: googlebot\nUser-agent: leadqualificationagent\n"
        "Disallow: /*.pdf$\nDisallow: /search*q=\n"
    )
    assert _robots_allows(robots, UA, "/brochure.pdf") is False
    assert _robots_allows(robots, UA, "/brochure.pdf.html") is True
    assert _robots_allows(robots, UA, "/search?q=freight") is False
    assert _robots_allows(robots, UA, "/about") is True


def test_robots_disallow_all_for_wildcard():
    assert _robots_allows("User-agent: *\nDisallow: /\n", UA, "/") is False


def test_robots_unrelated_group_does_not_apply():
    assert _robots_allows("User-agent: SomeOtherBot\nDisallow: /\n", UA, "/") is True
