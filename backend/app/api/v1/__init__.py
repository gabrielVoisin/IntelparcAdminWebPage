from fastapi import APIRouter

from app.api.v1 import (
    alarms,
    app_driver,
    auth,
    bay_qr,
    controllers,
    customers,
    device,
    reports,
    reservations,
    settings,
    sites,
    subscriptions,
    tariffs,
    team,
    ws,
)

api_router = APIRouter(prefix="/api/v1")
for module in (auth, app_driver, sites, bay_qr, alarms, reservations, subscriptions, customers, tariffs, reports, settings, team, controllers, device, ws):
    api_router.include_router(module.router)
