from django.db import models


class Place(models.Model):
    """A populated place from the US Census Gazetteer (offline geocoding)."""

    KIND_PLACE = "place"
    KIND_COUSUB = "cousub"
    KIND_CHOICES = [(KIND_PLACE, "Incorporated place / CDP"), (KIND_COUSUB, "County subdivision")]

    state = models.CharField(max_length=2)
    name = models.CharField(max_length=128)
    key = models.CharField(max_length=128, help_text="Normalized name used for lookups")
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    land_area = models.BigIntegerField(default=0)
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        indexes = [models.Index(fields=["state", "key"])]

    def __str__(self):
        return f"{self.name}, {self.state}"


class FuelStation(models.Model):
    """A truck stop from the provided OPIS price list, geocoded to its city."""

    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=128)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    # Lowest retail price reported for this truck stop in the file.
    price = models.DecimalField(max_digits=8, decimal_places=5)
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["lat", "lon"])]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
