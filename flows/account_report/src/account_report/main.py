"""The account report flow: one step, the report the review app asked for (by its id), written from final days."""

import json
import sys
from uuid import uuid4

from crewai.flow.flow import Flow, start
from pydantic import BaseModel, Field

from account_report.bootstrap import app


class ReportState(BaseModel):
    """Kickoff input: report_id, the report the review app saved (its accounts, dates, language and comment)."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    report_id: str = ""


class AccountReportFlow(Flow[ReportState]):
    """Writes one account report into the reports collection; the review app shows it and draws the PDF."""

    @start()
    def write_report(self) -> str:
        """Returns the report's title and overview (Markdown): the result shown on AMP and by /status."""
        return app().run(self.state.report_id)


def kickoff():
    """`bin/flow account_report kickoff '{"report_id": "<id>"}'`; AMP passes inputs itself."""
    inputs = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    AccountReportFlow().kickoff(inputs=inputs)


def plot():
    AccountReportFlow().plot()


if __name__ == "__main__":
    kickoff()
