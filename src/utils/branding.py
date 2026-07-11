class BrandingConfig:
    PROJECT_NAME = "SecureExam AI"
    LOGO_URL = "/static/assets/logo.png"
    FOOTER_TEXT = "© 2026 Secure Exam Platform. All Rights Reserved."
    PRIMARY_COLOR = "#3b82f6"
    SUPPORT_EMAIL = "support@secureexam.ai"
    
    @classmethod
    def get_context(cls):
        """
        Provides branding data to all templates via the context processor.
        """
        return {
            "project_name": cls.PROJECT_NAME,
            "branding": {
                "logo_url": cls.LOGO_URL,
                "footer_text": cls.FOOTER_TEXT,
                "primary_color": cls.PRIMARY_COLOR,
                "support_email": cls.SUPPORT_EMAIL
            }
        }