"""
Secrets detection for mememo.

Scans content for common secret patterns (API keys, passwords, tokens).
"""

import re

# Common secret patterns
SECRET_PATTERNS = [
    # API Keys
    (r'(?i)api[_-]?key[_-]?(?:=|:)\s*["\']?([a-zA-Z0-9]{20,})["\']?', "API Key"),
    (r'(?i)apikey[_-]?(?:=|:)\s*["\']?([a-zA-Z0-9]{20,})["\']?', "API Key"),
    # AWS
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
    (
        r'(?i)aws[_-]?secret[_-]?(?:access)?[_-]?key[_-]?(?:=|:)\s*["\']?([a-zA-Z0-9/+=]{40})["\']?',
        "AWS Secret",
    ),
    # GitHub
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token"),
    (r"ghu_[a-zA-Z0-9]{36}", "GitHub User-to-Server Token"),
    (r"ghs_[a-zA-Z0-9]{36}", "GitHub Server-to-Server Token"),
    (r"ghr_[a-zA-Z0-9]{36}", "GitHub Refresh Token"),
    # Generic tokens
    (r'(?i)token[_-]?(?:=|:)\s*["\']?([a-zA-Z0-9]{20,})["\']?', "Generic Token"),
    (r"(?i)bearer\s+([a-zA-Z0-9\-._~+/]+=*)", "Bearer Token"),
    # Passwords (basic detection)
    (r'(?i)password[_-]?(?:=|:)\s*["\']([^"\']{8,})["\']', "Password"),
    (r'(?i)passwd[_-]?(?:=|:)\s*["\']([^"\']{8,})["\']', "Password"),
    # Private keys
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "Private Key"),
    # Database connection strings
    (r"(?i)(?:mysql|postgres|mongodb)://[^\s]+:[^\s]+@[^\s]+", "Database Connection String"),
    # Slack tokens
    (r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24,}", "Slack Token"),
    # JWT tokens
    (r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", "JWT Token"),
]


class SecretsDetector:
    """Detects potential secrets in text content."""

    def __init__(self):
        """Initialize secrets detector with compiled patterns."""
        self.patterns = [(re.compile(pattern), name) for pattern, name in SECRET_PATTERNS]

    def scan(self, text: str) -> list[tuple[str, str, str]]:
        """
        Scan text for potential secrets.

        Args:
            text: Text to scan

        Returns:
            List of tuples (secret_type, matched_text, line_number)
        """
        findings = []
        lines = text.split("\n")

        for line_num, line in enumerate(lines, start=1):
            for pattern, secret_type in self.patterns:
                matches = pattern.finditer(line)
                for match in matches:
                    findings.append((secret_type, match.group(0), str(line_num)))

        return findings

    def has_secrets(self, text: str) -> bool:
        """
        Check if text contains any secrets.

        Args:
            text: Text to check

        Returns:
            True if secrets detected
        """
        return len(self.scan(text)) > 0

    def sanitize(self, text: str) -> str:
        """
        Sanitize text by redacting detected secrets.

        Args:
            text: Text to sanitize

        Returns:
            Text with secrets redacted
        """
        sanitized = text

        for pattern, secret_type in self.patterns:
            sanitized = pattern.sub(f"[REDACTED:{secret_type}]", sanitized)

        return sanitized

    def get_report(self, text: str) -> str:
        """
        Get a detailed report of detected secrets.

        Args:
            text: Text to analyze

        Returns:
            Human-readable report
        """
        findings = self.scan(text)

        if not findings:
            return "No secrets detected."

        report_lines = [f"Found {len(findings)} potential secret(s):"]
        for secret_type, matched_text, line_num in findings:
            # Truncate matched text for display
            display_text = matched_text[:50] + "..." if len(matched_text) > 50 else matched_text
            report_lines.append(f"  Line {line_num}: {secret_type} - {display_text}")

        return "\n".join(report_lines)
