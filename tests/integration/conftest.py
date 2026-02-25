import os

import pytest
from dotenv import load_dotenv

from src.tools.openemr_client import OpenEMRClient

load_dotenv()


@pytest.fixture
async def live_client():
    client = OpenEMRClient(
        base_url=os.environ.get("OPENEMR_BASE_URL", "http://localhost:80"),
        fhir_url=os.environ.get("OPENEMR_FHIR_URL", "http://localhost:80/apis/default/fhir"),
        client_id=os.environ.get("OPENEMR_CLIENT_ID", ""),
        client_secret=os.environ.get("OPENEMR_CLIENT_SECRET", ""),
    )
    yield client
    await client.close()
