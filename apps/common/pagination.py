"""Pagination sinflari.

Ikki xil pagination ishlatiladi:

* ``StandardPageNumberPagination`` — sahifa raqamli. Dizayndagi
  "Sahifa 1 / N" ko'rinishi va admin uslubidagi ro'yxatlar uchun.
* ``StandardCursorPagination`` — cheksiz scroll (shorts feed) uchun;
  yangi element qo'shilganda sahifa siljib ketmaydi.
"""
from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response


class StandardPageNumberPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "page": self.page.number,
                "page_size": self.get_page_size(self.request),
                "total_pages": self.page.paginator.num_pages,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )


class SmallPageNumberPagination(StandardPageNumberPagination):
    page_size = 8


class StandardCursorPagination(CursorPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 50
    ordering = "-created_at"
