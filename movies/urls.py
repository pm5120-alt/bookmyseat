from django.urls import path
from . import views

urlpatterns = [
    path("", views.movie_list, name="movie_list"),
    path("movie/<int:movie_id>/", views.movie_detail, name="movie_detail"),
    path("<int:movie_id>/theaters", views.theater_list, name="theater_list"),
    path("shows/<int:show_id>/seats/", views.seat_selection, name="seat_selection"),
    path("shows/<int:show_id>/seats/reserve/", views.reserve_seats, name="reserve_seats"),
    path("shows/<int:show_id>/availability/", views.seat_availability, name="seat_availability"),
    path("bookings/<uuid:booking_ref>/seats/", views.modify_reservation, name="modify_reservation"),
    path("bookings/<uuid:booking_ref>/payment/order/", views.create_payment_order, name="create_payment_order"),
    path("payments/<int:payment_id>/verify/", views.verify_payment, name="verify_payment"),
    path("payments/webhook/razorpay/", views.razorpay_webhook, name="razorpay_webhook"),
    path("bookings/<uuid:booking_ref>/ticket/", views.ticket_download, name="ticket_download"),
    path("bookings/<uuid:booking_ref>/cancel/", views.cancel_booking, name="cancel_booking"),
    path("tickets/verify/<str:token>/", views.verify_ticket, name="verify_ticket"),
    path("<int:movie_id>/reviews/", views.submit_review, name="submit_review"),
    path("reviews/<int:review_id>/edit/", views.edit_review, name="edit_review"),
    path("reviews/<int:review_id>/report/", views.report_review, name="report_review"),
    path("admin/analytics/", views.analytics_dashboard, name="analytics_dashboard"),
    path("admin/bookings.csv", views.export_bookings, name="export_bookings"),
    path("admin/analytics.csv", views.export_analytics_csv, name="export_analytics_csv"),
]
