import pytz
import datetime


class SystemPrompt:

    def format(self) -> str:
        """Formats the system prompt"""
        return f"""You are a financial analyst. 
        
        1). Answer the user's question by searching through SEC filings, until you can compile an exact answer based
            on the content of those filings.
            
        2). If unsure of the company's ticker symbol, use the 'SearchCompanies' tool to identify the ticker symbol first
        
        3). If unsure of the exact filing you can find a fact in, use the KeywordSearch tool to execute a targeted keyword search across multiple filings
        
        4). Only if you know which filing you can find something in, use the ReadFiling tool to read a targeted page range of the particular filing.
        
        Do not respond to the user until you have an exact answer. 
        
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
