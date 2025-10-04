import pytz
import datetime


class SystemPrompt:

    def format(self) -> str:
        """Formats the system prompt"""
        return f"""You are a financial analyst. 
        
        Answer questions by searching through SEC filings. 
        
        Today is {self.today()}
    """

    def today(self) -> str:
        """Returns the formatted current date and time"""
        dt = datetime.datetime.now(tz=pytz.timezone("US/Eastern"))
        return self.custom_date_format(dt=dt)

    @staticmethod
    def ordinal(n):
        suffix = ['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]
        if 11 <= (n % 100) <= 13:
            suffix = 'th'
        return str(n) + suffix

    def custom_date_format(self, dt: datetime.datetime):
        day = self.ordinal(dt.day)
        month = dt.strftime('%B')
        year = dt.year
        weekday = dt.strftime('%A')
        return f"{weekday}, {month} {day}, {year}"
