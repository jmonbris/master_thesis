import pandas as pd
import numpy as np
import os
from sklearn.metrics.pairwise import cosine_similarity
from utils import divide_into_three, get_categories


def load_data(
    file_path: str, 
    head: int | None = 500
) -> pd.DataFrame:
    """
    Loads data from CSV and JSON files
    
    Args:
        file_path: file path 
        head: number of rows to load
    """

    file_extension = file_path.split(".")[-1]
    if file_extension == "csv":
        df = pd.read_csv(file_path, nrows=head)
    elif file_extension == "json":
        df = pd.read_json(file_path, lines=True, orient='records', nrows=head)
    return df

def get_model_df(
    n_users: int | None = None, 
    sample_users: int = 100, 
    thresholds: tuple[int, int, int] = [5, 20, 50],
    dummy: bool = False, 
    seed: int | None = None,
    ignorant_proportion: float = 0.0
) -> pd.DataFrame:
    """
    Gets general model df with interactions between users and items

    Args:
        n_users: number of users to extract from CSV
        sample_users: number of users to sample (i.e. agents)
        dummy: load a pre-saved dummy dataset
        seed: random state seed for user sampling
    """
    
    print("Loading data...")

    try:
        base_path = os.path.dirname(__file__)
    except NameError:
        base_path = os.getcwd()

    file_path = os.path.join(base_path, "datasets/goodreads")
    
    if dummy:  # Preload for testing
        print("Dummy data read")
        return pd.read_csv(f"{file_path}/goodreads_interactions_sample.csv", index_col="index")
    
    # Load all items data

    df_items_raw = load_data(f"{file_path}/goodreads_book_genres_initial.json", None)
    df_items_non_empty = df_items_raw[df_items_raw["genres"].apply(lambda x: bool(x))]
    books = df_items_non_empty["book_id"]
    
    # Load all users data
    
    df_users_raw = load_data(f"{file_path}/goodreads_interactions.csv", n_users)
    df_users_with_books = df_users_raw[df_users_raw["book_id"].isin(books)]
    df_users_filtered = process_df_users_raw(
        df=df_users_with_books, n_users=sample_users, seed=seed, thresholds=thresholds, ignorant_proportion=ignorant_proportion
    )
    print("    - Users loaded")

    # Normalize rating

    df_users_filtered.loc[:, "rating"] = df_users_filtered["rating"].astype("float")
    df_users_filtered.loc[:, "rating"] = df_users_filtered["rating"] / 5.0
    
    # Filter items data

    unique_item_ids = df_users_filtered["book_id"].unique().tolist()
    df_items_filtered = df_items_non_empty.loc[df_items_non_empty["book_id"].isin(unique_item_ids)]
    print("    - Items loaded")
    
    # Get categories into columns
    
    df_items_filtered.loc[:, "genres"] = df_items_filtered["genres"].apply(reformat_dict)
    df_items_filtered_normalized = pd.json_normalize(df_items_filtered["genres"])
    df_items_result = pd.concat([df_items_filtered.reset_index(drop=True), df_items_filtered_normalized.reset_index(drop=True)], axis=1)
    
    # Combine dfs and return
    
    df_combined = pd.merge(df_users_filtered, df_items_result, how="inner", on="book_id")
    print(f"    - Model dataframe ready. Interactions: {len(df_combined)}")
    return df_combined.drop("genres", axis=1)

def process_df_users_raw(
    df: pd.DataFrame, 
    n_users: int, 
    seed: int | None, 
    thresholds: tuple[int, int, int],
    ignorant_proportion: float
) -> pd.DataFrame:
    # Filter to read-only entries
    read_only_df = df[df["is_read"] == 1]

    # Count books per user and filter users with up to top threshold books
    tmp_df = read_only_df.groupby("user_id")["book_id"].count().reset_index().rename(columns={"book_id": "book_count"})
    user_ids = tmp_df[tmp_df["book_count"] <= thresholds[2]]["user_id"].tolist()
    filtered_df = df[df["user_id"].isin(user_ids)]
    filtered_df = filtered_df.merge(tmp_df, on="user_id", how="left")

    # Divide users into three groups
    divisions = divide_into_three(n_users)

    # Sample from each user group
    low_df_user_ids = filtered_df[filtered_df["book_count"] <= thresholds[0]]
    low_user_ids = low_df_user_ids["user_id"].drop_duplicates().sample(n=divisions[0], random_state=seed)
    mid_df_user_ids = filtered_df[(filtered_df["book_count"] <= thresholds[1]) & (filtered_df["book_count"] > thresholds[0])]
    mid_user_ids = mid_df_user_ids["user_id"].drop_duplicates().sample(n=divisions[1], random_state=seed)
    high_df_user_ids = filtered_df[filtered_df["book_count"] > thresholds[1]]
    high_user_ids = high_df_user_ids["user_id"].drop_duplicates().sample(n=divisions[2], random_state=seed)

    # Concatenate samples into one DataFrame
    low_df = filtered_df[filtered_df["user_id"].isin(low_user_ids)]
    mid_df = filtered_df[filtered_df["user_id"].isin(mid_user_ids)]
    high_df = filtered_df[filtered_df["user_id"].isin(high_user_ids)]

    # Add ignorance
    if ignorant_proportion:
        for sub_df in [low_df, mid_df, high_df]:
            unique_users = sub_df['user_id'].drop_duplicates()
            shuffled_users = unique_users.sample(frac=1, random_state=seed)
            half_point = round(len(shuffled_users) * ignorant_proportion)
            naiveness_map = {user_id: True for user_id in shuffled_users[:half_point]}
            naiveness_map.update({user_id: False for user_id in shuffled_users[half_point:]})
            sub_df['ignorant'] = sub_df['user_id'].map(naiveness_map)
    else:
        for sub_df in [low_df, mid_df, high_df]:
            sub_df["ignorant"] = False

    return pd.concat([low_df, mid_df, high_df])

def reformat_dict(d: dict) -> dict:
    """
    Reformats dict from JSON as columns for df

    Args: 
        d: dictionary containing categories and their count
    """
    
    genres = {}
    for genre, value in d.items():
        for g in genre.split(","):
            g = g.strip().replace(" ", "_").replace("-", "_")
            genres[g] = genres.get(g, 0) + value if value > 0 else 0  # Some genres had a -1, so they were removed
    genres.update({k: 0 for k in get_categories() if k not in genres})
    return genres

def get_items_df(df: pd.DataFrame, priority: str | None = None) -> pd.DataFrame:
    """
    Get aggregated items df
    
    Args:
        df: interactions dataframe
        priority: item priority strategy
    """
    
    print("Getting items dataframe...")
    cat_cols = get_categories()
    aggregations = {
        "is_read": "sum",
        "rating": "mean",
        "is_reviewed": "sum"
    }
    aggregations.update({k: "mean" for k in cat_cols})
    tmp_df = df.copy().drop("user_id", axis=1)
    items_df = tmp_df.groupby(by=["book_id"]).agg(aggregations)
    items_df["vector"] = items_df.apply(lambda row: np.array(row[cat_cols]).reshape(1, -1), axis=1)
    items_df = items_df.drop(cat_cols, axis=1)
    items_df["priority"] = items_df.apply(calculate_priority, args=(priority,), axis=1)
    print(f"    - Items dataframe ready. Items: {len(items_df)}")
    return items_df

def calculate_priority(row: pd.Series, priority: str | float | None = None) -> float:
    """
    Calculate priority column for items
    
    Args:
        row: items dataframe row
        priority: priority strategy
    """

    categories = get_categories()
    if not priority:
        return 0
    elif isinstance(priority, float):
        return float(np.random.random() < priority)
    elif priority in categories:
        cat_index = categories.index(priority)
        max_value = np.max(row["vector"])
        max_indices = np.where(row["vector"] == max_value)[1]
        return float(cat_index in max_indices and not np.all(row["vector"] == 0))

def get_users_df(
    df: pd.DataFrame, 
    df_items: pd.DataFrame, 
    steps: int, 
    thresholds: tuple[int, int, int],
    n_recs: int
) -> pd.DataFrame:
    """
    Get aggregated users df
    
    Args: 
        df: interactions dataframe
        df_items: items dataframe
        steps: steps in simulation
        thresholds: books per year limit for low-mid and mid-high reader personas 
    """
    
    print("Getting users dataframe...")
    tmp_df = df.copy()
    cat_cols = get_categories()
    for col in cat_cols:
        tmp_df[col] = tmp_df[col].apply(lambda x: 1 if x > 0 else 0)
    aggregations = {
        "is_reviewed": "sum",
        "is_read": "sum",
        "rating": "mean",
        "book_id": lambda x: list(x),
        "ignorant": "first"
    }
    aggregations.update({k: "sum" for k in cat_cols})
    
    # Calculate probabilities of reading for each user

    low_readers_average = thresholds[0] / 2
    mid_readers_average = (thresholds[1] - thresholds[0]) / 2 + thresholds[0]
    high_readers_average = (thresholds[2] - thresholds[1]) / 2 + thresholds[1]
    low_readers_proba = round(low_readers_average / steps, 4)
    mid_readers_proba = round(mid_readers_average / steps, 4)
    high_readers_proba = round(high_readers_average / steps, 4)

    users_df = tmp_df.groupby(by=["user_id"]).agg(aggregations)
    users_df["vector"] = users_df.apply(lambda row: np.array(row[cat_cols]).reshape(1, -1), axis=1)
    users_df = users_df.drop(cat_cols, axis=1)
    users_df["book_id"] = users_df.apply(calculate_book_score, args=(df_items,), axis=1)
    users_df["read_proba"] = np.where(
        users_df["is_read"] <= thresholds[0], 
        low_readers_proba, 
        np.where(
            users_df["is_read"] <= thresholds[1], 
            mid_readers_proba, 
            high_readers_proba
        )
    )
    if n_recs:
        users_df = matrix_cosine_similarity(users_df, df_items, n_recs)
    else:
        users_df["similarities"] = None
    print(f"    - Users dataframe ready. Users: {len(users_df)}")
    return users_df

def matrix_cosine_similarity(df_reference: pd.DataFrame, df_compare: pd.DataFrame, n: int = 50) -> dict:
    """Calculates cosine similarity between each vector of df_reference and all
    vectors of df_compare via matrix multiplication
    
    Args:
        df_reference: reference df
        df_compare: reference df
        n: number of books with scores sorted by similarity
    """
    # Calculate matrixes and similarites

    tmp_reference = df_reference["vector"].apply(lambda x: x.reshape(16).astype(float))
    tmp_compare = df_compare["vector"].apply(lambda x: x.reshape(16).astype(float))
    matrix_reference = np.stack(tmp_reference.values)
    matrix_compare = np.stack(tmp_compare.values)
    norms_reference = np.linalg.norm(matrix_reference, axis=1)
    norms_compare = np.linalg.norm(matrix_compare, axis=1)
    matrix_reference_normalized = matrix_reference / norms_reference[:, np.newaxis]
    matrix_compare_normalized = matrix_compare / norms_compare[:, np.newaxis]
    similarities = np.dot(matrix_reference_normalized, matrix_compare_normalized.T)
    
    # Add similarities as column to reference df
    
    df_reference["similarities"] = [
        dict(sorted({df_compare.index[j]: similarities[i, j] for j in range(len(df_compare))}.items(),
            key=lambda item: item[1], reverse=True)[:n])
        for i in range(len(df_reference))
    ]
    return df_reference

def calculate_book_score(row: pd.Series, df_items: pd.DataFrame) -> dict:
    """
    Calculate item scores based on cosine similarity with users
    
    Args:
        row: users dataframe row
        df_items: items dataframe
    """

    books = {}
    user_vector = row["vector"]
    for book_id in row["book_id"]:
        item = df_items[df_items.index == book_id]
        if item["priority"].item() > 0:
            books.update({book_id: item["priority"].item()})
        else:
            item_vector = item["vector"].item()
            similarity = cosine_similarity(user_vector, item_vector)
            books.update({book_id: similarity[0][0]})
    return books
