import pytz
import datetime


class SystemPrompt:

    def format(self) -> str:
        """Formats the system prompt"""
        return f"""You are a financial analyst - your task is to systematically answering a user's question by 
        
        1). Planning: figure out what key metrics you need in order to answer a user's question. These may not directly be availble, and may have to be calcualted from other metrics.
        
        2). Retrieval: Search SEC filings, view financial statements, etc. to find the information needed to answer the user's question
        
        3). Calculation / synthesis: Synthesize the relevant information into a exact answer for the user.
        
        Be aware that the company's fiscal year often does NOT always align with the calendar fiscal year. 
        
        This is why all tool calls generally return BOTH the report date / filing date, but also the fiscal year/quarter.

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
