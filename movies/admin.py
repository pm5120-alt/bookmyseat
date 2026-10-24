from django.contrib import admin
from .models import (Booking, BookingSeat, CastMember, City, Genre, Language, Movie,
                     MoviePoster, Payment, Review, ReviewReport, Screen, Seat, Show,
                     ShowSeat, Theater)

class PosterInline(admin.TabularInline): model = MoviePoster; extra = 0
class CastInline(admin.TabularInline): model = CastMember; extra = 0

@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ["name", "release_date", "rating", "review_count", "certification"]
    search_fields = ["name", "description", "cast"]
    list_filter = ["release_date", "certification", "genres", "languages"]
    filter_horizontal = ["genres", "languages"]
    inlines = [PosterInline, CastInline]

@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin): list_display = ["name", "city", "movie", "time"]; search_fields = ["name", "address"]; list_filter = ["city"]
@admin.register(Show)
class ShowAdmin(admin.ModelAdmin): list_display = ["movie", "theater", "screen", "starts_at", "price", "is_active"]; list_filter = ["is_active", "starts_at", "theater__city"]; search_fields = ["movie__name", "theater__name"]
@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin): list_display = ["screen", "seat_number", "is_booked"]; list_filter = ["screen__theater"]
@admin.register(ShowSeat)
class ShowSeatAdmin(admin.ModelAdmin): list_display = ["show", "seat", "status", "held_by", "hold_expires_at"]; list_filter = ["status"]
@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin): list_display = ["reference", "user", "movie", "show", "status", "total_amount", "booked_at"]; list_filter = ["status", "booked_at"]; search_fields = ["reference", "user__username"]
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin): list_display = ["booking", "status", "amount", "transaction_id", "created_at"]; list_filter = ["status"]
@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["movie", "user", "rating", "is_hidden", "created_at"]
    list_filter = ["is_hidden", "rating"]
    def has_add_permission(self, request): return False
@admin.register(ReviewReport)
class ReviewReportAdmin(admin.ModelAdmin): list_display = ["review", "reporter", "reason", "resolved_at"]; list_filter = ["resolved_at"]
for model in (City, Genre, Language, Screen): admin.site.register(model)
