"""Import every ORM model so relationship targets are registered in each process."""

# RQ workers do not import the HTTP routers, so they need the model registry
# assembled explicitly before a service queries SQLAlchemy.
from app.modules.activities import models as activities_models  # noqa: F401
from app.modules.calls import models as calls_models  # noqa: F401
from app.modules.identity import models as identity_models  # noqa: F401
from app.modules.leads import models as leads_models  # noqa: F401
from app.modules.notifications import models as notifications_models  # noqa: F401
from app.modules.outreach import models as outreach_models  # noqa: F401
from app.modules.pipeline import models as pipeline_models  # noqa: F401
from app.modules.prospection import models as prospection_models  # noqa: F401
from app.modules.rag import models as rag_models  # noqa: F401
from app.modules.seller_profile import models as seller_profile_models  # noqa: F401
from app.modules.validation import models as validation_models  # noqa: F401
