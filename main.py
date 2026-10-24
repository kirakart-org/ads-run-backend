
import os
import logging
from typing import Optional, Literal
from urllib.parse import parse_qsl, unquote, urlsplit

import psycopg2
import psycopg2.errors

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, HttpUrl

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =========================================================
# FASTAPI APPLICATION
# =========================================================

app = FastAPI(
    title="Kirakart API",
    description="Kirakart early access and Kirakart Ads enquiry API",
    version="1.1.0",
)


# =========================================================
# CORS
# Configure FRONTEND_ORIGINS in your environment.
# =========================================================

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# =========================================================
# DATABASE CONNECTION
# SUPABASE_DB_URL must be a PostgreSQL connection string.
# =========================================================

def get_conn():
    database_url = os.getenv("SUPABASE_DB_URL")
    
    if not database_url:
        raise RuntimeError(
            "SUPABASE_DB_URL is not configured."
        )

    try:
        parsed_url = urlsplit(database_url)
        if parsed_url.scheme not in ("postgres", "postgresql") or not parsed_url.hostname:
            raise ValueError("Invalid PostgreSQL connection URL.")

        connection_options = dict(parse_qsl(parsed_url.query))
        connection_options.update(
            {
                "host": parsed_url.hostname,
                "port": parsed_url.port or 5432,
                "dbname": unquote(parsed_url.path.lstrip("/")),
                "user": unquote(parsed_url.username or ""),
                "password": unquote(parsed_url.password or ""),
                "sslmode": "require",
                "connect_timeout": 10,
            }
        )
    except ValueError as exc:
        raise RuntimeError("SUPABASE_DB_URL is not a valid PostgreSQL URL.") from exc

    return psycopg2.connect(
        **connection_options,
    )


# =========================================================
# COMMON DATABASE ERROR HANDLING
# =========================================================

def database_http_error(conn, exc):
    if conn:
        conn.rollback()

    if isinstance(exc, psycopg2.OperationalError):
        logger.error("Database connection failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Database is temporarily unavailable.",
        )

    if isinstance(exc, psycopg2.errors.UniqueViolation):
        raise HTTPException(
            status_code=409,
            detail="This mobile number or email is already registered.",
        )

    logger.error("Database operation failed: %s", type(exc).__name__)

    raise HTTPException(
        status_code=500,
        detail="The database operation could not be completed.",
    )


# =========================================================
# REQUEST MODEL: EARLY ACCESS
# =========================================================

class EarlyAccess(BaseModel):
    mobile: str = Field(min_length=5, max_length=20)
    email: Optional[EmailStr] = None

    # Customer
    name: Optional[str] = Field(default=None, max_length=150)

    # Merchant
    owner_name: Optional[str] = Field(default=None, max_length=150)
    shop_name: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = Field(default=None, max_length=100)
    subcategory: Optional[str] = Field(default=None, max_length=100)
    business_type: Optional[str] = Field(default=None, max_length=100)

    # Location
    address: Optional[str] = Field(default=None, max_length=1000)
    locality: Optional[str] = Field(default=None, max_length=150)
    city: Optional[str] = Field(default=None, max_length=100)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)

    # User type
    purpose: Literal["shopper", "shop_owner"]


# =========================================================
# REQUEST MODEL: KIRAKART ADS ENQUIRY
# =========================================================

class AdEnquiry(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    business_email: EmailStr
    brand_name: str = Field(min_length=1, max_length=200)

    product_url: Optional[HttpUrl] = None

    product_category: str = Field(min_length=1, max_length=100)
    selected_package: str = Field(
        default="not_sure",
        max_length=100,
    )
    approximate_budget: str = Field(
        min_length=1,
        max_length=50,
    )
    desired_video_count: Optional[int] = Field(
        default=None,
        gt=0,
        le=1000,
    )
    project_details: str = Field(
        min_length=10,
        max_length=10000,
    )


# =========================================================
# ROOT / HEALTH CHECK
# =========================================================

@app.get("/")
def root():
    return {
        "success": True,
        "message": "Kirakart API is running successfully.",
        "version": "1.1.0",
    }


@app.get("/health")
def health():
    return {
        "success": True,
        "status": "healthy",
    }


# =========================================================
# EARLY ACCESS: CUSTOMER AND MERCHANT REGISTRATION
# Existing endpoint retained.
# =========================================================

@app.post("/early-access")
def early_access(data: EarlyAccess):
    conn = None
    cur = None

    try:
        conn = get_conn()
        cur = conn.cursor()

        if data.purpose == "shopper":
            cur.execute(
                """
                INSERT INTO public.early_access_customers
                    (mobile_number, name, email)
                VALUES (%s, %s, %s)
                RETURNING id;
                """,
                (
                    data.mobile.strip(),
                    data.name.strip() if data.name else None,
                    str(data.email) if data.email else None,
                ),
            )

            record_id = cur.fetchone()[0]
            conn.commit()

            return {
                "success": True,
                "type": "customer",
                "message": (
                    "Welcome to Kirakart! "
                    "You are on the early-access list."
                ),
                "id": str(record_id),
            }

        if data.purpose == "shop_owner":
            if not data.owner_name or not data.owner_name.strip():
                raise HTTPException(
                    status_code=400,
                    detail="Owner name is required.",
                )

            if not data.shop_name or not data.shop_name.strip():
                raise HTTPException(
                    status_code=400,
                    detail="Shop name is required.",
                )

            if not data.category or not data.category.strip():
                raise HTTPException(
                    status_code=400,
                    detail="Business category is required.",
                )

            if not data.address or not data.address.strip():
                raise HTTPException(
                    status_code=400,
                    detail="Business address is required.",
                )

            if not data.city or not data.city.strip():
                raise HTTPException(
                    status_code=400,
                    detail="City is required.",
                )

            cur.execute(
                """
                INSERT INTO public.early_access_merchants (
                    owner_name,
                    mobile_number,
                    email,
                    shop_name,
                    category,
                    subcategory,
                    business_type,
                    address,
                    locality,
                    city,
                    latitude,
                    longitude
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                RETURNING id;
                """,
                (
                    data.owner_name.strip(),
                    data.mobile.strip(),
                    str(data.email) if data.email else None,
                    data.shop_name.strip(),
                    data.category.strip(),
                    data.subcategory.strip() if data.subcategory else None,
                    data.business_type.strip() if data.business_type else None,
                    data.address.strip(),
                    data.locality.strip() if data.locality else None,
                    data.city.strip(),
                    data.latitude,
                    data.longitude,
                ),
            )

            record_id = cur.fetchone()[0]
            conn.commit()

            return {
                "success": True,
                "type": "merchant",
                "message": (
                    "Your shop has been added to "
                    "the Kirakart early-access list."
                ),
                "id": str(record_id),
            }

        raise HTTPException(
            status_code=400,
            detail="Unsupported registration purpose.",
        )

    except HTTPException:
        if conn:
            conn.rollback()
        raise

    except psycopg2.DatabaseError as exc:
        database_http_error(conn, exc)

    except Exception:
        if conn:
            conn.rollback()

        logger.exception("Unexpected early-access error.")
        raise HTTPException(
            status_code=500,
            detail="An unexpected server error occurred.",
        )

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


# =========================================================
# KIRAKART ADS: CREATE ENQUIRY
# Requires public.ad_enquiries table.
# =========================================================

@app.post("/ad-enquiries", status_code=201)
def create_ad_enquiry(data: AdEnquiry):
    conn = None
    cur = None

    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO public.ad_enquiries (
                name,
                business_email,
                brand_name,
                product_url,
                product_category,
                selected_package,
                approximate_budget,
                desired_video_count,
                project_details
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, status, created_at;
            """,
            (
                data.name.strip(),
                str(data.business_email),
                data.brand_name.strip(),
                str(data.product_url) if data.product_url else None,
                data.product_category.strip(),
                data.selected_package.strip(),
                data.approximate_budget.strip(),
                data.desired_video_count,
                data.project_details.strip(),
            ),
        )

        record = cur.fetchone()
        conn.commit()

        return {
            "success": True,
            "message": "Your project enquiry has been received.",
            "enquiry": {
                "id": record[0],
                "status": record[1],
                "created_at": record[2].isoformat(),
            },
        }

    except HTTPException:
        if conn:
            conn.rollback()
        raise

    except psycopg2.DatabaseError as exc:
        database_http_error(conn, exc)

    except Exception:
        if conn:
            conn.rollback()

        logger.exception("Unexpected ad enquiry error.")
        raise HTTPException(
            status_code=500,
            detail="An unexpected server error occurred.",
        )

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
